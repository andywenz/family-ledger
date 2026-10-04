"""表定义。本地开发与测试据此建表；CDK（D5）必须与此保持一致。"""

from __future__ import annotations

from typing import Any

MAIN_TABLE_DEF: dict[str, Any] = {
    "AttributeDefinitions": [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "KEYS_ONLY"},
        }
    ],
    "BillingMode": "PAY_PER_REQUEST",
}

JOURNAL_TABLE_DEF: dict[str, Any] = {
    "AttributeDefinitions": [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
    ],
    "KeySchema": [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
    "BillingMode": "PAY_PER_REQUEST",
}


def create_tables(client: Any, main: str, journal: str) -> None:
    existing = set(client.list_tables()["TableNames"])
    if main not in existing:
        client.create_table(TableName=main, **MAIN_TABLE_DEF)
    if journal not in existing:
        client.create_table(TableName=journal, **JOURNAL_TABLE_DEF)
    for name in (main, journal):
        client.get_waiter("table_exists").wait(TableName=name)
