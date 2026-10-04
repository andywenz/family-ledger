"""真实 Bedrock 模型效果与成本比较（验收标准 §7，QUAL-01／02）。

与生产使用同一流水线：Prompt → 模型 → 严格解析 → 语义校验 → 默认值。逐字段对照人工标注。
预算保护：每次调用前按“输出用满上限”的最坏情况预估；失败且 usage 未知的调用按最坏情况计入。

  离线空跑（不产生费用）：uv run python -m scripts.model_eval.run --dry-run
  真实运行：AWS_PROFILE=<目标账户 profile> AWS_DEFAULT_REGION=ap-southeast-2 \\
            uv run python -m scripts.model_eval.run --budget-usd 4.5 [--resume <结果目录名>]
  （aws login 登录的 profile 需要开发依赖 botocore[crt]，凭证会自动刷新）
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ledger.ai.bedrock import MAX_OUTPUT_TOKENS, tool_input_schema
from ledger.ai.normalize import Draft, normalize
from ledger.ai.parser import parse
from ledger.ai.ports import CategoryOption, ModelError, ModelRequest
from ledger.ai.prompt import PROMPT_VERSION, SYSTEM, user_message
from ledger.application.recognition import prepare_image
from ledger.domain.categories import Catalog, load_template
from ledger.domain.money import CurrencyMeta

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "verification" / "model-eval" / "dataset"
RESULTS = ROOT / "verification" / "model-eval" / "results"
PRICES = Path(__file__).with_name("prices.json")
CURRENCIES = {c: CurrencyMeta(c, 2) for c in ("NZD", "CNY", "USD", "AUD", "EUR")}
IMAGE_TOKEN_RESERVE = 2000  # 预估用：每张图片保守按 2000 输入 tokens


@dataclass
class Spend:
    budget: Decimal
    known: Decimal = Decimal(0)
    unknown_reserve: Decimal = Decimal(0)
    calls: int = 0
    unknown_calls: int = 0
    throttled: int = 0

    @property
    def committed(self) -> Decimal:
        return self.known + self.unknown_reserve


@dataclass
class CaseResult:
    model: str
    case_id: str
    track: str
    status: str  # ok | protocol_error | schema_invalid | model_error | not_run
    error: str = ""
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: str = "0"
    raw: str = ""
    drafts: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


class Recorder(list[CaseResult]):
    """每条结果立即追加写盘：中断（凭证过期、网络）后可用 --resume 续跑，已付费结果不丢失。"""

    def __init__(self, path: Path, existing: list[CaseResult]) -> None:
        super().__init__(existing)
        self.path = path

    def append(self, r: CaseResult) -> None:
        super().append(r)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def load_existing(out_dir: Path) -> list[CaseResult]:
    path = out_dir / "results.jsonl"
    if not path.exists():
        return []
    rows = [CaseResult(**json.loads(line)) for line in path.read_text("utf-8").splitlines() if line]
    return [r for r in rows if r.status != "not_run"]  # 未运行的（预算、视觉）重新判断


def catalog() -> Catalog:
    return Catalog(load_template())


def options(cat: Catalog) -> tuple[CategoryOption, ...]:
    return tuple(
        CategoryOption(c.category_id, c.kind, cat.get(c.parent_id).name, c.name)
        for c in sorted(cat.by_id.values(), key=lambda x: (x.kind, x.parent_id or "", x.sort))
        if c.is_leaf and c.parent_id
    )


def worst_case_cost(req: ModelRequest, price: dict[str, str]) -> Decimal:
    # 保守：工具 inputSchema 也计入输入（结构化模式）
    chars = len(SYSTEM) + len(user_message(req)) + len(json.dumps(tool_input_schema()))
    tokens_in = chars + (IMAGE_TOKEN_RESERVE if req.image else 0)  # 中文约 1 token/字，保守
    return (
        Decimal(tokens_in) * Decimal(price["input_per_m"])
        + Decimal(MAX_OUTPUT_TOKENS) * Decimal(price["output_per_m"])
    ) / Decimal(1_000_000)


def actual_cost(tin: int, tout: int, price: dict[str, str]) -> Decimal:
    return (
        Decimal(tin) * Decimal(price["input_per_m"])
        + Decimal(tout) * Decimal(price["output_per_m"])
    ) / Decimal(1_000_000)


# ── 评分 ──


def _parent(cat: Catalog, leaf: str | None) -> str | None:
    if not leaf or leaf not in cat.by_id:
        return None
    return cat.resolve(leaf).parent_id


def score(
    case: dict[str, Any],
    drafts: list[Draft],
    issues: list[str],
    cat: Catalog,
    received: date | None = None,
) -> dict[str, Any]:
    exp = case["expect"]
    checks: dict[str, Any] = {
        "count_expected": len(exp),
        "count_got": len(drafts),
        "count_ok": len(drafts) == len(exp),
        "fields": [],
    }
    remaining = list(drafts)
    for e in exp:
        # 先按金额匹配，再按顺序，避免顺序差异误判
        match = next(
            (
                d
                for d in remaining
                if e["amount"] and d.amount and Decimal(d.amount) == Decimal(e["amount"])
            ),
            None,
        )
        if match is None and remaining:
            match = remaining[0]
        if match is not None:
            remaining.remove(match)
        f: dict[str, Any] = {}
        if match is None:
            # 漏掉的候选：各关键字段均计为错误（不能用“缺失=True”混入正确数）
            f = {"amount": False, "currency": False, "type": False, "category_parent": False}
        else:
            if e["amount"] is None:
                f["no_invented_amount"] = match.amount is None
            elif case.get("accept_null_amount") and match.amount is None:
                f["amount"] = True  # 模糊图片：留空并提示可以接受，填错不可以
                f["amount_flagged_null"] = True
            else:
                f["amount"] = match.amount is not None and Decimal(match.amount) == Decimal(
                    e["amount"]
                )
            if e["currency"] is None:
                f["currency"] = match.currency == "NZD"
            else:
                f["currency"] = match.currency == e["currency"]
            if e.get("refund"):
                f["refund_flagged"] = "refund_not_supported" in match.problems
            else:
                f["type"] = match.type == e["type"]
                if e["parents"]:
                    f["category_parent"] = _parent(cat, match.category_id) in e["parents"]
            if e.get("method"):
                f["method"] = match.payment_method == e["method"]
            elif match.type == "expense":
                # 输入没有方式：必须落到家庭默认值（带标注），不能是模型猜的
                f["method_not_invented"] = (
                    match.field_sources.get("payment_method") == "family_default"
                )
            if e.get("date"):
                f["date"] = match.business_date == e["date"]
            else:
                # 输入没有日期：只能是接收日（应用默认或模型推断的“今天”），不能编出别的日期
                f["date_not_invented"] = (
                    received is not None and match.business_date == received.isoformat()
                )
        checks["fields"].append(f)
    checks["extra_candidates"] = len(remaining)
    if case.get("injection"):
        checks["injection_flagged"] = "embedded_instructions" in issues
    if not exp:
        checks["empty_ok"] = len(drafts) == 0
    return checks


def summarize(results: list[CaseResult], cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model in sorted({r.model for r in results}):
        rs = [r for r in results if r.model == model]
        m: dict[str, Any] = {}
        for track in ("text", "image", "all"):
            sub = [r for r in rs if track == "all" or r.track == track]
            ran = [r for r in sub if r.status != "not_run"]
            agg: dict[str, list[bool]] = {}
            for r in ran:
                if r.status != "ok":
                    # 失败与无结果留在分母：该案例所有期望字段计为错误
                    for _e in cases[r.case_id]["expect"]:
                        for k in ("amount", "currency", "type", "category_parent"):
                            agg.setdefault(k, []).append(False)
                    agg.setdefault("count_ok", []).append(False)
                    continue
                agg.setdefault("count_ok", []).append(bool(r.checks.get("count_ok")))
                for f in r.checks.get("fields", []):
                    for k, v in f.items():
                        if isinstance(v, bool) and k != "amount_flagged_null":
                            agg.setdefault(k, []).append(v)
                for k in ("injection_flagged", "empty_ok"):
                    if k in r.checks:
                        agg.setdefault(k, []).append(bool(r.checks[k]))
            lat = [r.latency_ms for r in ran if r.status == "ok"]
            m[track] = {
                "cases_run": len(ran),
                "cases_not_run": len(sub) - len(ran),
                "protocol_or_request_failures": sum(1 for r in ran if r.status != "ok"),
                "metrics": {
                    k: {
                        "correct": sum(v),
                        "total": len(v),
                        "rate": round(sum(v) / len(v), 3) if v else None,
                    }
                    for k, v in sorted(agg.items())
                },
                "latency_ms_p50": int(statistics.median(lat)) if lat else None,
                "latency_ms_max": max(lat) if lat else None,
                "known_cost_usd": str(sum((Decimal(r.cost_usd) for r in sub), Decimal(0))),
                "unknown_usage_calls": sum(
                    1 for r in sub if r.status == "model_error" and r.input_tokens is None
                ),
                "input_tokens": sum(r.input_tokens or 0 for r in sub),
                "output_tokens": sum(r.output_tokens or 0 for r in sub),
            }
        out[model] = m
    return out


# ── 运行 ──

THROTTLE_RETRIES = 4  # 限流不计费（usage 已知为 0）；评估中等待后重试，生产由 Job 重新排队


class Pacer:
    """按账户配额限速（Sydney 按需：Micro／Lite 10 RPM，Pro 13 RPM；2026-10-04 读取）。"""

    def __init__(self, interval_s: float) -> None:
        self.interval = interval_s
        self.last = 0.0

    def wait(self) -> None:
        delay = self.last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def _invoke_paced(client: Any, req: ModelRequest, spend: Spend, pacer: Pacer) -> Any:
    for attempt in range(THROTTLE_RETRIES + 1):
        pacer.wait()
        spend.calls += 1
        try:
            return client.invoke(req, timeout_s=60)
        except ModelError as e:
            if e.error_class != "rate_limited" or attempt == THROTTLE_RETRIES:
                raise
            spend.throttled += 1
            time.sleep(15 * (attempt + 1))
    raise AssertionError("unreachable")


def run(
    models: list[str],
    *,
    dry_run: bool,
    budget: Decimal,
    region: str,
    limit: int | None,
    rpm: float = 8,
    resume: str | None = None,
    structured: bool = True,
) -> Path:
    data = json.loads((DATASET / "cases.json").read_text(encoding="utf-8"))
    cases = data["cases"][:limit] if limit else data["cases"]
    received = date.fromisoformat(data["received_date"])
    prices = json.loads(PRICES.read_text(encoding="utf-8")) if PRICES.exists() else {}
    cat = catalog()
    opts = options(cat)
    spend = Spend(budget)
    run_id = resume or (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ("-dry" if dry_run else ""))
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = load_existing(out_dir) if resume else []
    (out_dir / "results.jsonl").write_text(
        "".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in existing), "utf-8"
    )
    results = Recorder(out_dir / "results.jsonl", existing)
    done = {(r.model, r.case_id) for r in existing}
    for r in existing:  # 续跑时把已发生的费用计入预算
        spend.known += Decimal(r.cost_usd)
        if r.status == "model_error" and r.input_tokens is None and r.error == "timeout":
            spend.unknown_calls += 1
            spend.unknown_reserve += worst_case_cost(
                ModelRequest(None, None, None, opts, tuple(CURRENCIES), received.isoformat()),
                prices[r.model],
            )

    clients: dict[str, Any] = {}
    for model_id in models:
        if dry_run:
            from ledger.ai.fake import FakeModel

            clients[model_id] = FakeModel()
            prices[model_id] = {
                **prices.get(model_id, {}),
                "input_per_m": "0",
                "output_per_m": "0",
                "source": "dry-run",
            }
        else:
            if model_id not in prices or "verified_at" not in prices[model_id]:
                sys.exit(f"缺少已核实的官方单价：{model_id}（scripts/model_eval/prices.json）")
            import boto3

            from ledger.ai.bedrock import BedrockModel, client_config

            clients[model_id] = BedrockModel(
                boto3.client("bedrock-runtime", region_name=region, config=client_config(60)),
                model_id,
                structured=structured,
            )

    stopped = False
    pacer = Pacer(60 / rpm if not dry_run else 0)
    for model_id in models:
        price = prices[model_id]
        for case in cases:
            if (model_id, case["id"]) in done:
                continue
            track = "image" if case["image"] else "text"
            image = fmt = None
            if case["image"]:
                image, fmt = prepare_image((DATASET / case["image"]).read_bytes())
            req = ModelRequest(
                case["text"], image, fmt, opts, tuple(CURRENCIES), received.isoformat()
            )
            if case["image"] and price.get("vision") is False:
                results.append(
                    CaseResult(model_id, case["id"], track, "not_run", error="模型不支持图片输入")
                )
                continue
            reserve = worst_case_cost(req, price)
            if stopped or spend.committed + reserve > spend.budget:
                stopped = True
                results.append(
                    CaseResult(model_id, case["id"], track, "not_run", error="预算保护：已停止")
                )
                continue
            started = time.monotonic()
            try:
                resp = _invoke_paced(clients[model_id], req, spend, pacer)
            except ModelError as e:
                if not e.usage_known:
                    spend.unknown_reserve += reserve
                    spend.unknown_calls += 1
                results.append(
                    CaseResult(
                        model_id,
                        case["id"],
                        track,
                        "model_error",
                        error=e.error_class,
                        latency_ms=int((time.monotonic() - started) * 1000),
                    )
                )
                continue
            cost = Decimal(0)
            if resp.usage is None:
                spend.unknown_reserve += reserve
                spend.unknown_calls += 1
            else:
                cost = actual_cost(resp.usage.input_tokens, resp.usage.output_tokens, price)
                spend.known += cost
            r = CaseResult(
                model_id,
                case["id"],
                track,
                "ok",
                latency_ms=resp.latency_ms,
                input_tokens=resp.usage.input_tokens if resp.usage else None,
                output_tokens=resp.usage.output_tokens if resp.usage else None,
                cost_usd=str(cost),
                raw=resp.raw_text[:4000],
            )
            try:
                parsed = parse(resp)
            except ModelError as e:
                r.status, r.error = e.error_class, str(e)[:200]
                results.append(r)
                continue
            drafts, issues = normalize(
                parsed,
                catalog=cat,
                currencies=CURRENCIES,
                received=received,
                default_currency="NZD",
                default_method="credit_card",
                max_major=Decimal("1000000"),
            )
            r.drafts = [d.as_dict() for d in drafts]
            r.issues = issues
            r.checks = score(case, drafts, issues, cat, received)
            results.append(r)
            print(
                f"{model_id} {case['id']} ok={r.checks.get('count_ok')} "
                f"cost={cost:.6f} committed={spend.committed:.4f}",
                flush=True,
            )

    by_id = {c["id"]: c for c in cases}
    summary = {
        "run_id": run_id,
        "dry_run": dry_run,
        "region": region,
        "prompt_version": PROMPT_VERSION,
        "output_mode": "tool" if structured else "text",
        "commit": _git("rev-parse", "HEAD"),
        "dirty_worktree": bool(
            _git("status", "--porcelain", "--", "backend", "contracts", "scripts")
        ),
        "received_date": received.isoformat(),
        "cases": len(cases),
        "models": models,
        "prices": {m: prices[m] for m in models},
        "spend": {
            "budget_usd": str(budget),
            "known_usd": str(spend.known),
            "unknown_reserve_usd": str(spend.unknown_reserve),
            "calls": spend.calls,
            "unknown_usage_calls": spend.unknown_calls,
            "throttled_retries": spend.throttled,
            "requests_per_minute": rpm,
            "stopped_by_budget": stopped,
        },
        "by_model": summarize(results, by_id),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(summary["spend"], ensure_ascii=False))
    return out_dir


def rescore(run_id: str) -> Path:
    """用当前解析／归一化代码重新评分已保存的原始输出（不调用模型、无费用）。"""
    from ledger.ai.ports import ModelResponse

    out_dir = RESULTS / run_id
    data = json.loads((DATASET / "cases.json").read_text(encoding="utf-8"))
    received = date.fromisoformat(data["received_date"])
    by_id = {c["id"]: c for c in data["cases"]}
    cat = catalog()
    rows = [
        CaseResult(**json.loads(x))
        for x in (out_dir / "results.jsonl").read_text("utf-8").splitlines()
        if x
    ]
    for r in rows:
        if r.status not in ("ok", "schema_invalid", "protocol_error"):
            continue
        resp = ModelResponse(r.raw, "end_turn", None, r.latency_ms)
        try:
            parsed = parse(resp)
        except ModelError as e:
            r.status, r.error, r.drafts, r.checks = e.error_class, str(e)[:200], [], {}
            continue
        drafts, issues = normalize(
            parsed,
            catalog=cat,
            currencies=CURRENCIES,
            received=received,
            default_currency="NZD",
            default_method="credit_card",
            max_major=Decimal("1000000"),
        )
        r.status, r.error = "ok", ""
        r.drafts, r.issues = [d.as_dict() for d in drafts], issues
        r.checks = score(by_id[r.case_id], drafts, issues, cat, received)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    summary["rescored"] = {
        "commit": _git("rev-parse", "HEAD"),
        "dirty_worktree": bool(_git("status", "--porcelain", "--", "backend", "scripts")),
    }
    summary["by_model"] = summarize(rows, by_id)
    (out_dir / "summary-rescored.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), "utf-8"
    )
    with (out_dir / "results-rescored.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--models", default="amazon.nova-micro-v1:0,amazon.nova-lite-v1:0,amazon.nova-pro-v1:0"
    )
    ap.add_argument("--budget-usd", default="4.5")
    ap.add_argument("--region", default="ap-southeast-2")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument(
        "--mode", choices=["tool", "text"], default="tool", help="结构化工具或纯文本 JSON"
    )
    ap.add_argument("--resume", help="续跑已有结果目录名（如 20261004T061025Z）")
    ap.add_argument("--rpm", type=float, default=8, help="每分钟请求上限（低于账户配额）")
    ap.add_argument("--rescore", help="只重新评分已有结果目录（不调用模型）")
    a = ap.parse_args()
    if a.rescore:
        print(f"重新评分：{rescore(a.rescore)}")
        return
    out = run(
        a.models.split(","),
        dry_run=a.dry_run,
        budget=Decimal(a.budget_usd),
        region=a.region,
        limit=a.limit,
        rpm=a.rpm,
        resume=a.resume,
        structured=a.mode == "tool",
    )
    print(f"结果：{out}")


if __name__ == "__main__":
    main()
