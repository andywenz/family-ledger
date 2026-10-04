"""DynamoDB 存储执行器：类型转换、强一致读取、带逐项失败映射的事务。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from ledger.domain.errors import DomainError, Retryable, UpstreamUnknown

_ser = TypeSerializer()
_de = TypeDeserializer()

Item = dict[str, Any]
FailFactory = Callable[[Item | None], DomainError]


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else v
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, set):
        return {_plain(x) for x in v}
    return v


def serialize(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(v) for k, v in item.items() if v is not None}


def deserialize(raw: Mapping[str, Any]) -> Item:
    return {k: _plain(_de.deserialize(v)) for k, v in raw.items()}


@dataclass
class TxItem:
    """事务中的一项。on_fail 在该项条件失败时构造业务错误（参数为当前项，若可读取）。"""

    op: str  # Put | Update | Delete | ConditionCheck
    params: dict[str, Any]
    on_fail: FailFactory | None = None


@dataclass
class Tx:
    table: str
    items: list[TxItem] = field(default_factory=list)

    def _key(self, pk: str, sk: str) -> dict[str, Any]:
        return serialize({"PK": pk, "SK": sk})

    def put(
        self,
        item: Mapping[str, Any],
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
        on_fail: FailFactory | None = None,
    ) -> None:
        p: dict[str, Any] = {"TableName": self.table, "Item": serialize(item)}
        _cond(p, condition, names, values)
        self.items.append(TxItem("Put", p, on_fail))

    def put_new(self, item: Mapping[str, Any], on_fail: FailFactory | None = None) -> None:
        self.put(item, condition="attribute_not_exists(PK)", on_fail=on_fail)

    def update(
        self,
        pk: str,
        sk: str,
        expression: str,
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
        on_fail: FailFactory | None = None,
    ) -> None:
        p: dict[str, Any] = {
            "TableName": self.table,
            "Key": self._key(pk, sk),
            "UpdateExpression": expression,
        }
        _cond(p, condition, names, values)
        self.items.append(TxItem("Update", p, on_fail))

    def delete(
        self,
        pk: str,
        sk: str,
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
        on_fail: FailFactory | None = None,
    ) -> None:
        p: dict[str, Any] = {"TableName": self.table, "Key": self._key(pk, sk)}
        _cond(p, condition, names, values)
        self.items.append(TxItem("Delete", p, on_fail))

    def check(
        self,
        pk: str,
        sk: str,
        condition: str,
        *,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
        on_fail: FailFactory | None = None,
    ) -> None:
        p: dict[str, Any] = {"TableName": self.table, "Key": self._key(pk, sk)}
        _cond(p, condition, names, values)
        self.items.append(TxItem("ConditionCheck", p, on_fail))

    def keys(self) -> list[tuple[str, str]]:
        out = []
        for it in self.items:
            raw = it.params.get("Key") or {
                "PK": it.params["Item"]["PK"],
                "SK": it.params["Item"]["SK"],
            }
            out.append((raw["PK"]["S"], raw["SK"]["S"]))
        return out


def _cond(
    p: dict[str, Any],
    condition: str | None,
    names: Mapping[str, str] | None,
    values: Mapping[str, Any] | None,
) -> None:
    if condition:
        p["ConditionExpression"] = condition
    if names:
        p["ExpressionAttributeNames"] = dict(names)
    if values:
        p["ExpressionAttributeValues"] = serialize(values)


class Store:
    MAX_TX_ITEMS = 100

    def __init__(self, table: str, journal_table: str, client: Any) -> None:
        self.table = table
        self.journal_table = journal_table
        self.client = client

    @classmethod
    def connect(
        cls,
        table: str,
        journal_table: str,
        endpoint: str | None = None,
        region: str = "ap-southeast-2",
    ) -> Store:
        cfg = Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=2,
            read_timeout=5,
        )
        kwargs: dict[str, Any] = {"region_name": region, "config": cfg}
        if endpoint:
            # 仅本地：DynamoDB Local 接受任意凭证
            kwargs["endpoint_url"] = endpoint
            kwargs["aws_access_key_id"] = "local"
            kwargs["aws_secret_access_key"] = "local"  # noqa: S105
        return cls(table, journal_table, boto3.client("dynamodb", **kwargs))

    def tx(self) -> Tx:
        return Tx(self.table)

    # ── 读取（全部强一致） ──

    def get(self, pk: str, sk: str) -> Item | None:
        r = self.client.get_item(
            TableName=self.table, Key=serialize({"PK": pk, "SK": sk}), ConsistentRead=True
        )
        return deserialize(r["Item"]) if "Item" in r else None

    def query_prefix(
        self,
        pk: str,
        prefix: str,
        *,
        descending: bool = False,
        limit: int | None = None,
        start_key: Mapping[str, Any] | None = None,
    ) -> tuple[list[Item], dict[str, Any] | None]:
        # 空前缀表示整个分区（DynamoDB 不接受空字符串作为 begins_with 参数）
        cond, vals = (
            ("PK = :pk AND begins_with(SK, :p)", {":pk": pk, ":p": prefix})
            if prefix
            else ("PK = :pk", {":pk": pk})
        )
        p: dict[str, Any] = {
            "TableName": self.table,
            "KeyConditionExpression": cond,
            "ExpressionAttributeValues": serialize(vals),
            "ConsistentRead": True,
            "ScanIndexForward": not descending,
        }
        if limit:
            p["Limit"] = limit
        if start_key:
            p["ExclusiveStartKey"] = serialize(start_key)
        r = self.client.query(**p)
        lek = deserialize(r["LastEvaluatedKey"]) if "LastEvaluatedKey" in r else None
        return [deserialize(i) for i in r.get("Items", [])], lek

    def query_all(self, pk: str, prefix: str, *, descending: bool = False) -> Iterator[Item]:
        start: dict[str, Any] | None = None
        while True:
            items, start = self.query_prefix(pk, prefix, descending=descending, start_key=start)
            yield from items
            if start is None:
                return

    # ── 事务 ──

    def commit(self, tx: Tx, *, token_seed: str | None = None) -> None:
        """提交事务。

        - 条件失败：读取失败项当前状态，按 on_fail 抛出业务错误；
        - 并发冲突：抛出 Retryable，由调用方重新读取后重算；
        - 发送后超时／连接中断：抛出 UpstreamUnknown（结果未知）。
        """
        if not tx.items:
            return
        if len(tx.items) > self.MAX_TX_ITEMS:
            raise DomainError("事务项超过上限", code="internal")
        keys = tx.keys()
        if len(set(keys)) != len(keys):
            raise DomainError("同一事务不能重复操作同一项", code="internal")
        req: dict[str, Any] = {"TransactItems": [{it.op: it.params} for it in tx.items]}
        if token_seed:
            req["ClientRequestToken"] = hashlib.sha256(token_seed.encode()).hexdigest()[:36]
        try:
            self.client.transact_write_items(**req)
        except EndpointConnectionError as e:
            # 连接未建立，请求未到达服务端
            raise DomainError("数据库暂不可用", code="internal") from e
        except (ReadTimeoutError, ConnectionClosedError) as e:
            raise UpstreamUnknown("提交结果未知，请按动作 ID 查询") from e
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "TransactionCanceledException":
                self._raise_cancel(tx, e, keys)
            if code in (
                "TransactionInProgressException",
                "ProvisionedThroughputExceededException",
                "ThrottlingException",
                "RequestLimitExceeded",
            ):
                raise Retryable(code) from e
            if code == "IdempotentParameterMismatchException":
                raise DomainError("事务令牌冲突", code="internal") from e
            raise

    def _raise_cancel(self, tx: Tx, e: ClientError, keys: list[tuple[str, str]]) -> None:
        reasons = e.response.get("CancellationReasons") or []
        codes = [r.get("Code") for r in reasons]
        for idx, c in enumerate(codes):
            if c == "ConditionalCheckFailed":
                item = tx.items[idx]
                current = self.get(*keys[idx])
                if item.on_fail is not None:
                    raise item.on_fail(current) from e
                raise DomainError(
                    "数据已变化，请刷新后重试", code="version_conflict", current=None
                ) from e
        if any(c in ("TransactionConflict", "ThrottlingError") for c in codes):
            raise Retryable("TransactionConflict") from e
        raise DomainError(f"事务被取消：{codes}", code="internal") from e


def query_between(store: Store, pk: str, low: str, high: str) -> list[Item]:
    """SK 在 [low, high] 范围内的全部项（强一致）。"""
    out: list[Item] = []
    start: dict[str, Any] | None = None
    while True:
        p: dict[str, Any] = {
            "TableName": store.table,
            "KeyConditionExpression": "PK = :pk AND SK BETWEEN :a AND :b",
            "ExpressionAttributeValues": serialize({":pk": pk, ":a": low, ":b": high}),
            "ConsistentRead": True,
        }
        if start:
            p["ExclusiveStartKey"] = serialize(start)
        r = store.client.query(**p)
        out.extend(deserialize(i) for i in r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            return out
        start = deserialize(r["LastEvaluatedKey"])
