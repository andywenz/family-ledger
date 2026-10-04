"""历史 Excel 账目导入（分类与历史导入规则 §5；运维命令，非请求路径）。

三步，人工核对在中间：
  1. preview：读取原 Excel → 按私人映射生成预览表（xlsx），
     列出类型、两级分类、方式、原事项与异常提示。
  2. 用户在预览表里核对、修改，或把“导入”列改为“否”。
  3. import：读取核对后的预览表 → 用当前家庭目录解析分类 → 逐条调用与网站相同的 create_entry
     （source=import，按消费日期汇率快照折算）→ reconcile 独立核对。

规则：
- 原 Excel 不改写；真实文件、映射与预览表只放在 config/private/（不进仓库）。
- 只认“日期为日期、金额为数值”的行；表头、汇总与空行跳过，其余非空行列为“跳过”供核对。
- 负数不凭符号猜类型：映射为收入时取绝对值；与映射类型冲突的行默认不导入并提示。
- 幂等：每行动作 ID ＝ 文件摘要＋工作表序号＋行号＋内容指纹；重复执行命中原结果，不重复入账。
- 每行独立事务（小批），中断后重跑会跳过已提交的行。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

PREVIEW_COLUMNS = [
    "工作表",
    "行号",
    "日期",
    "类型",
    "一级分类",
    "二级分类",
    "币种",
    "金额",
    "方式",
    "备注",
    "原表折算NZD",
    "原表折算CNY",
    "提示",
    "导入",
    "指纹",
]
TYPES = {"支出": "expense", "收入": "income"}
TYPE_LABELS = {v: k for k, v in TYPES.items()}
METHOD_LABELS = {"credit_card": "信用卡", "debit_card": "借记卡", "cash": "现金"}
METHODS = {v: k for k, v in METHOD_LABELS.items()}
UNKNOWN = {"expense": ("其他支出", "待分类"), "income": ("其他收入", "待分类")}


@dataclass(frozen=True)
class SourceRow:
    sheet_index: int
    sheet: str
    row: int
    business_date: dt.date
    item: str
    currency: str
    amount: Decimal  # 原表金额（可能为负）
    orig_nzd: Decimal | None
    orig_cny: Decimal | None
    channel: str

    @property
    def fingerprint(self) -> str:
        parts = (self.sheet, self.row, self.business_date, self.item, self.currency, self.amount)
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:10]


@dataclass
class PreviewRow:
    sheet_index: int
    sheet: str
    row: int
    business_date: dt.date
    type: str  # expense | income
    parent: str
    leaf: str
    currency: str
    amount: Decimal  # 正数
    method: str | None
    note: str
    orig_nzd: Decimal | None
    orig_cny: Decimal | None
    hints: list[str] = field(default_factory=list)
    include: bool = True
    fingerprint: str = ""


def _dec(v: Any) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        return None
    return Decimal(str(v))


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_workbook(path: Path) -> tuple[list[SourceRow], list[tuple[str, int]]]:
    """返回有效记录与被跳过的非空行（工作表、行号）。遍历真实单元格，不信任 dimension。"""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    rows: list[SourceRow] = []
    skipped: list[tuple[str, int]] = []
    for si, ws in enumerate(wb.worksheets, 1):
        for ri, r in enumerate(ws.iter_rows(min_col=1, max_col=7, values_only=True), 1):
            if ri == 1:
                continue  # 表头
            d, item, cur, amt, nzd, cny, channel = (list(r) + [None] * 7)[:7]
            amount = _dec(amt)
            if isinstance(d, (dt.datetime, dt.date)) and amount is not None:
                day = d.date() if isinstance(d, dt.datetime) else d
                rows.append(
                    SourceRow(
                        si,
                        ws.title,
                        ri,
                        day,
                        str(item or "").strip(),
                        str(cur or "").strip().upper(),
                        amount,
                        _dec(nzd),
                        _dec(cny),
                        str(channel or "").strip(),
                    )
                )
            elif any(c not in (None, "") for c in r):
                skipped.append((ws.title, ri))
    return rows, skipped


def build_preview(rows: list[SourceRow], mapping: dict[str, Any]) -> list[PreviewRow]:
    items: dict[str, Any] = mapping.get("items", {})
    channels: dict[str, str] = mapping.get("channels", {})
    out: list[PreviewRow] = []
    for s in rows:
        hints: list[str] = []
        include = True
        m = items.get(s.item)
        if m is None:
            etype = "income" if s.amount < 0 else "expense"
            parent, leaf = UNKNOWN[etype]
            hints.append("映射中没有此事项，已归入待分类")
        else:
            etype = m["type"]
            parent, leaf = m["category"]
        if s.amount < 0 and etype != "income":
            hints.append("负数金额但映射为支出：请确认类型（默认不导入）")
            include = False
        if s.amount > 0 and etype == "income" and m is not None:
            hints.append("正数金额但映射为收入：请确认（默认不导入）")
            include = False
        if s.amount == 0:
            hints.append("金额为 0（不导入）")
            include = False
        method = channels.get(s.channel)
        if method is None:
            hints.append(f"未知方式“{s.channel}”：请选择")
        out.append(
            PreviewRow(
                s.sheet_index,
                s.sheet,
                s.row,
                s.business_date,
                etype,
                parent,
                leaf,
                s.currency,
                abs(s.amount),
                method,
                s.item,
                s.orig_nzd,
                s.orig_cny,
                hints,
                include,
                s.fingerprint,
            )
        )
    return out


def write_preview(rows: list[PreviewRow], path: Path) -> None:
    import openpyxl
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "预览"
    ws.append(PREVIEW_COLUMNS)
    for r in rows:
        ws.append(
            [
                r.sheet,
                r.row,
                r.business_date,
                TYPE_LABELS[r.type],
                r.parent,
                r.leaf,
                r.currency,
                float(r.amount),
                METHOD_LABELS.get(r.method or "", ""),
                r.note,
                float(r.orig_nzd) if r.orig_nzd is not None else None,
                float(r.orig_cny) if r.orig_cny is not None else None,
                "；".join(r.hints),
                "是" if r.include else "否",
                f"{r.sheet_index}:{r.fingerprint}",
            ]
        )
    for col, opts in (("D", "支出,收入"), ("I", "信用卡,借记卡,现金"), ("N", "是,否")):
        dv = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{len(rows) + 1}")
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        row[0].number_format = "yyyy-mm-dd"
    widths = [8, 6, 12, 6, 14, 16, 6, 10, 8, 28, 12, 12, 36, 6, 16]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = "A2"
    wb.save(path)


def read_preview(path: Path) -> list[PreviewRow]:
    import openpyxl

    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    header = [c.value for c in ws[1]]
    if header[: len(PREVIEW_COLUMNS)] != PREVIEW_COLUMNS:
        raise ValueError("预览表表头被改动，请用原预览表核对")
    out: list[PreviewRow] = []
    for ri, r in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if all(c in (None, "") for c in r[:15]):
            continue
        (
            sheet,
            row,
            day,
            tlabel,
            parent,
            leaf,
            cur,
            amt,
            mlabel,
            note,
            onzd,
            ocny,
            hint,
            inc,
            fp,
        ) = r[:15]
        problems = []
        if tlabel not in TYPES:
            problems.append("类型")
        amount = _dec(amt)
        if amount is None or amount <= 0:
            problems.append("金额")
        if not isinstance(day, (dt.datetime, dt.date)):
            problems.append("日期")
        if inc not in ("是", "否"):
            problems.append("导入")
        if mlabel not in (None, "", *METHODS):
            problems.append("方式")
        if problems:
            raise ValueError(f"预览表第 {ri} 行无效：{'、'.join(problems)}")
        idx, _, fpr = str(fp).partition(":")
        assert isinstance(day, (dt.datetime, dt.date)) and amount is not None
        out.append(
            PreviewRow(
                int(idx),
                str(sheet),
                int(row),
                day.date() if isinstance(day, dt.datetime) else day,
                TYPES[str(tlabel)],
                str(parent or "").strip(),
                str(leaf or "").strip(),
                str(cur or "").strip().upper(),
                amount,
                METHODS.get(str(mlabel or "")),
                str(note or "").strip(),
                _dec(onzd),
                _dec(ocny),
                [str(hint)] if hint else [],
                inc == "是",
                fpr,
            )
        )
    return out


def action_key(batch: str, r: PreviewRow) -> str:
    return f"hist-{batch[:12]}-{r.sheet_index}-{r.row}-{r.fingerprint}"


def entry_note(r: PreviewRow) -> str:
    orig = "／".join(
        f"{c} {v}" for c, v in (("NZD", r.orig_nzd), ("CNY", r.orig_cny)) if v is not None
    )
    tail = f"｜历史导入 {r.sheet}!{r.row}" + (f"；原表折算 {orig}" if orig else "")
    return (r.note[: 200 - len(tail)] + tail)[:200]


def resolve_leaves(catalog: Any, rows: list[PreviewRow]) -> dict[tuple[str, str, str], str]:
    """按（类型目录、一级名、二级名）在当前家庭目录中找有效叶子；找不到即报错，不自建分类。"""
    from ledger.domain.categories import KIND_FOR_ENTRY_TYPE

    live = [c for c in catalog.by_id.values() if c.status == "active" and c.redirect_to is None]
    names = {c.category_id: c.name for c in live}
    index = {
        (c.kind, names.get(c.parent_id or "", ""), c.name): c.category_id for c in live if c.is_leaf
    }
    out: dict[tuple[str, str, str], str] = {}
    missing = set()
    for r in rows:
        if not r.include:
            continue
        k = (KIND_FOR_ENTRY_TYPE[r.type], r.parent, r.leaf)
        if k in index:
            out[k] = index[k]
        else:
            missing.add(f"{TYPE_LABELS[r.type]}／{r.parent}／{r.leaf}")
    if missing:
        raise ValueError("家庭目录中找不到这些分类：" + "，".join(sorted(missing)))
    return out


@dataclass
class ImportReport:
    created: int = 0
    replayed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


def import_rows(
    ctx: Any, actor: Any, fid: str, rows: list[PreviewRow], *, batch: str, dry_run: bool = False
) -> ImportReport:
    from ledger.application import entries, repo
    from ledger.application.actions import Scope, get_receipt
    from ledger.domain.categories import KIND_FOR_ENTRY_TYPE
    from ledger.domain.entries import EntryInput

    leaves = resolve_leaves(repo.catalog(ctx, fid), rows)
    rep = ImportReport()
    for r in rows:
        if not r.include:
            rep.skipped += 1
            continue
        key = action_key(batch, r)
        data = EntryInput(
            business_date=r.business_date.isoformat(),
            type=r.type,
            amount=str(r.amount),
            leaf_category_id=leaves[(KIND_FOR_ENTRY_TYPE[r.type], r.parent, r.leaf)],
            currency=r.currency,
            note=entry_note(r),
            payment_method=r.method,
        )
        if dry_run:
            repo.resolve_snapshot(ctx, fid, r.business_date, r.currency)  # 汇率可用性
            continue
        existed = get_receipt(ctx, Scope.family(fid, actor, key)) is not None
        try:
            entries.create_entry(ctx, actor, fid, key, data, source="import")
        except Exception as e:  # noqa: BLE001 - 逐行记录，继续其余行；重跑会补齐
            rep.failed.append(f"{r.sheet}!{r.row}：{e}")
            continue
        if existed:
            rep.replayed += 1  # 同一动作 ID 已提交：命中原结果，不重复入账
        else:
            rep.created += 1
    return rep


def reconcile(ctx: Any, fid: str, rows: list[PreviewRow]) -> dict[str, Any]:
    """独立核对：按月×类型×币种比较“预览表应导入”与“账本中 source=import 的有效账目”。"""
    from ledger.application import repo

    def key(month: str, etype: str, cur: str) -> str:
        return f"{month} {TYPE_LABELS.get(etype, etype)} {cur}"

    expected: dict[str, list[Decimal]] = defaultdict(list)
    for r in rows:
        if r.include:
            expected[key(r.business_date.strftime("%Y-%m"), r.type, r.currency)].append(r.amount)
    months = sorted({r.business_date.strftime("%Y-%m") for r in rows if r.include})
    currencies = repo.family_currencies(ctx, fid) | repo.global_currencies(ctx)
    actual: dict[str, list[Decimal]] = defaultdict(list)
    nzd_total = Decimal(0)
    for m in months:
        for e in repo.month_entries(ctx, fid, m):
            if e.source != "import":
                continue
            digits = currencies[e.currency].minor_digits
            actual[key(m, e.type, e.currency)].append(Decimal(e.amount_minor).scaleb(-digits))
            nzd_total += Decimal(e.nzd_minor).scaleb(-2) * (1 if e.type == "expense" else -1)
    lines = []
    ok = True
    for k in sorted(set(expected) | set(actual)):
        ce, ca = len(expected[k]), len(actual[k])
        se, sa = sum(expected[k], Decimal(0)), sum(actual[k], Decimal(0))
        match = ce == ca and se == sa
        ok &= match
        lines.append(
            {
                "组": k,
                "应导入笔数": ce,
                "已导入笔数": ca,
                "应导入金额": str(se),
                "已导入金额": str(sa),
                "一致": match,
            }
        )
    return {"一致": ok, "明细": lines, "已导入净支出NZD（按消费日汇率）": str(nzd_total)}


# ── 命令行 ──


def _store_ctx(local: bool) -> Any:
    from ledger.adapters.dynamo.store import Store
    from ledger.application.context import AppContext

    region = (
        os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-southeast-2"
    )
    table = os.environ["LEDGER_TABLE"]
    journal = os.environ["LEDGER_DELETION_TABLE"]
    endpoint = os.environ.get("DYNAMODB_ENDPOINT") if local else None
    return AppContext(store=Store.connect(table, journal, endpoint=endpoint, region=region))


def _actor_and_family(ctx: Any, login: str, family_name: str) -> tuple[Any, str]:
    from ledger.adapters.dynamo import keys
    from ledger.application import families
    from ledger.application.users import user_id_by_login
    from ledger.domain.authz import Actor

    uid = user_id_by_login(ctx, login)
    if uid is None:
        raise SystemExit(f"找不到登录名 {login}")
    p = ctx.store.get(keys.user(uid), keys.PROFILE)
    if p is None:
        raise SystemExit(f"{login} 没有账号资料")
    actor = Actor(
        uid,
        int(p.get("session_epoch", 0)),
        bool(p.get("is_system_admin")),
        int(p.get("version", 1)),
    )
    fams = [f for f in families.list_my_families(ctx, actor) if f["name"] == family_name]
    if len(fams) != 1:
        raise SystemExit(f"{login} 所在家庭中名为“{family_name}”的有 {len(fams)} 个")
    return actor, fams[0]["family_id"]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="历史 Excel 导入")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("preview")
    p1.add_argument("--file", type=Path, required=True)
    p1.add_argument("--mapping", type=Path, required=True)
    p1.add_argument("--out", type=Path, required=True)
    for name in ("import", "reconcile"):
        p = sub.add_parser(name)
        p.add_argument("--file", type=Path, required=True, help="原 Excel（用于批次摘要）")
        p.add_argument("--preview", type=Path, required=True)
        p.add_argument("--login", required=True)
        p.add_argument("--family", required=True, help="家庭名称")
        p.add_argument("--account", help="生产：凭证账户必须等于此 ID")
        if name == "import":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "preview":
        rows, skipped = read_workbook(a.file)
        mapping = json.loads(a.mapping.read_text(encoding="utf-8"))
        prev = build_preview(rows, mapping)
        write_preview(prev, a.out)
        c = Counter((r.business_date.strftime("%Y-%m"), r.type) for r in prev)
        print(f"读取 {len(rows)} 条有效记录，跳过非空行 {len(skipped)}；预览表：{a.out}")
        print("按月／类型：", {f"{m} {TYPE_LABELS[t]}": n for (m, t), n in sorted(c.items())})
        flagged = [r for r in prev if r.hints]
        print(f"需要核对的行 {len(flagged)}，默认不导入 {sum(not r.include for r in prev)}")
        return

    local = os.environ.get("LEDGER_ENV") == "local"
    if not local:
        import boto3

        if not a.account:
            raise SystemExit("生产环境必须提供 --account")
        actual = boto3.client("sts").get_caller_identity()["Account"]
        if actual != a.account:
            raise SystemExit(f"当前凭证属于账户 {actual}，与 --account 不符；已停止")
    ctx = _store_ctx(local)
    actor, fid = _actor_and_family(ctx, a.login, a.family)
    prows = read_preview(a.preview)
    batch = file_digest(a.file)
    if a.cmd == "reconcile":
        print(json.dumps(reconcile(ctx, fid, prows), ensure_ascii=False, indent=1))
        return
    n = sum(r.include for r in prows)
    target = "本地演练表" if local else f"生产账户 {a.account}"
    print(f"将以 {a.login} 身份向家庭“{a.family}”导入 {n} 条（{target}，批次 {batch[:12]}）。")
    if not a.dry_run and not a.yes and input("输入 IMPORT 继续：").strip() != "IMPORT":
        raise SystemExit("已取消")
    rep = import_rows(ctx, actor, fid, prows, batch=batch, dry_run=a.dry_run)
    if a.dry_run:
        print(f"演算通过：{n} 条分类可解析、汇率可用；未写入。")
        return
    print(
        f"新建 {rep.created}，重复执行命中 {rep.replayed}，"
        f"跳过 {rep.skipped}，失败 {len(rep.failed)}"
    )
    for f in rep.failed:
        print("  失败：", f)
    print(json.dumps(reconcile(ctx, fid, prows), ensure_ascii=False, indent=1))
    if rep.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
