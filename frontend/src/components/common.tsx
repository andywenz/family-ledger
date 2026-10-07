import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { tr } from "../lib/i18n";
import { ApiError, UnknownOutcome, api, newKey } from "../api/client";
import { Icon } from "./Icon";

/** 页面标题：直接写页面名称，副标题说明这页能做什么（不加装饰性的英文眉标）。 */
export function PageHeading({ title, subtitle, actions }: { eyebrow?: string; title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}

const fieldLabels = (): Record<string, string> => ({
  amount: tr("金额", "Amount"), business_date: tr("日期", "Date"), payment_method: tr("方式", "Payment method"),
  leaf_category_id: tr("分类", "Category"), currency: tr("币种", "Currency"), note: tr("备注", "Note"),
  refund_of: tr("原消费", "Original expense"), name: tr("名称", "Name"),
});

/** 统一错误展示：显示服务端中文信息与字段，不展示他人对象内容。 */
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  if (error instanceof UnknownOutcome) return null; // 由 UnknownPanel 处理
  const e = error instanceof ApiError ? error : new ApiError(0, "internal", String(error));
  const fields = e.details?.fields ?? [];
  return (
    <div className="notice error-notice" role="alert">
      <Icon name="info" />
      <div>
        <strong>{e.message}</strong>
        {fields.length > 0 && (
          <ul>
            {fields.map((f) => (
              <li key={f.field + f.code}>
                {fieldLabels()[f.field] ?? f.field}：{f.code}
              </li>
            ))}
          </ul>
        )}
        {e.code === "version_conflict" && <p>{tr("这条记录已被他人修改，请刷新后再改。", "Someone else changed this record. Refresh and try again.")}</p>}
        {onRetry && (
          <button type="button" className="text-button" onClick={onRetry}>
            {tr("重新加载", "Reload")}
          </button>
        )}
      </div>
    </div>
  );
}

/** 写操作：同一次用户操作的重试复用同一 Idempotency-Key；结果未知时提供查询入口。 */
export function useAction<T>(familyId?: string) {
  const keyRef = useRef<string>(newKey());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [unknown, setUnknown] = useState<UnknownOutcome | null>(null);

  const run = useCallback(async (fn: (key: string) => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      const out = await fn(keyRef.current);
      keyRef.current = newKey(); // 成功后下一次操作用新 key
      setUnknown(null);
      return out;
    } catch (e) {
      if (e instanceof UnknownOutcome) setUnknown(e);
      else {
        setError(e);
        // 确定未提交（业务拒绝）时，下一次尝试可以换新 key
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) keyRef.current = newKey();
      }
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);

  const check = useCallback(async () => {
    if (!unknown) return "missing" as const;
    const path = familyId ? `/families/${familyId}/actions/${unknown.key}` : `/me/actions/${unknown.key}`;
    try {
      await api.get(path);
      setUnknown(null);
      keyRef.current = newKey();
      return "committed" as const;
    } catch (e) {
      if (e instanceof ApiError && e.code === "action_not_found") return "not_committed" as const;
      return "unknown" as const;
    }
  }, [unknown, familyId]);

  return { run, busy, error, setError, unknown, check };
}

export function UnknownPanel({ action, onCommitted, onRetry }: { action: ReturnType<typeof useAction>; onCommitted: () => void; onRetry: () => void }) {
  const [msg, setMsg] = useState("");
  if (!action.unknown) return null;
  return (
    <div className="notice unknown-notice" role="alert">
      <Icon name="info" />
      <div>
        <strong>{tr("网络中断，暂时无法确认是否已保存。", "The connection dropped, so we can't confirm whether it was saved.")}</strong>
        <p>{tr("请先查询结果；未保存时再重试，系统不会重复记账。", "Check the result first. If it wasn't saved, retry — it won't be recorded twice.")}</p>
        <div className="form-actions compact">
          <button type="button" className="button secondary" onClick={async () => {
            const r = await action.check();
            if (r === "committed") onCommitted();
            else setMsg(r === "not_committed" ? tr("尚未保存，可以重试。", "Not saved yet. You can retry.") : tr("仍无法确认，请稍后再查。", "Still can't confirm. Check again shortly."));
          }}>{tr("查询结果", "Check result")}</button>
          <button type="button" className="button primary" onClick={onRetry}>{tr("用同一请求重试", "Retry the same request")}</button>
        </div>
        {msg && <p>{msg}</p>}
      </div>
    </div>
  );
}

export function useToast() {
  const [text, setText] = useState("");
  useEffect(() => {
    if (!text) return;
    const t = setTimeout(() => setText(""), 3200);
    return () => clearTimeout(t);
  }, [text]);
  const node = (
    <div id="toast" role="status" aria-live="polite" className={text ? "visible" : ""}>
      {text}
    </div>
  );
  return { show: setText, node };
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await load());
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog ref={ref} id="record-dialog" className={wide ? "wide" : ""} aria-label={title} onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <div className="dialog-heading">
        <h2>{title}</h2>
        <button type="button" className="icon-button" aria-label={tr("关闭", "Close")} onClick={onClose}>×</button>
      </div>
      {children}
    </dialog>
  );
}
