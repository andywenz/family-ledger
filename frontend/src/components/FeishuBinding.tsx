import { useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, useAction } from "./common";

/** 飞书私聊记账绑定（ADR-0013）：一次性绑定码、默认家庭、解绑。 */
export function FeishuBinding() {
  const { me, reloadMe } = useAuth();
  const families = me?.families ?? [];
  const binding = me?.feishu ?? { status: "unbound" };
  const [fid, setFid] = useState(binding.default_family_id ?? families[0]?.family_id ?? "");
  const [code, setCode] = useState<{ code: string; expires_at: string } | null>(null);
  const action = useAction<unknown>();
  if (!me) return null;

  const statusLabel = binding.status === "active" ? "已绑定" : binding.status === "suspended" ? "已暂停" : "未绑定";
  return (
    <section className="card">
      <div className="section-head"><h2>飞书记账</h2><span className="badge">{statusLabel}</span></div>
      <p className="muted">绑定后，在飞书私聊机器人发送文字或小票照片，在卡片中核对、修改并确认入账。</p>
      {binding.status === "suspended" && <p className="tiny-note warn">密码变更后飞书记账已暂停，请重新生成绑定码完成绑定。</p>}
      {families.length === 0 && <p className="tiny-note warn">请先加入或创建一个家庭。</p>}
      {families.length > 0 && (
        <label className="field">默认家庭（飞书消息记到这里）
          <select value={fid} onChange={async (e) => {
            setFid(e.target.value);
            if (binding.status === "active" && binding.version) {
              const r = await action.run((key) => api.patch("/me/feishu-binding", { default_family_id: e.target.value, expected_version: binding.version }, { key }));
              if (r !== undefined) await reloadMe();
            }
          }}>
            {families.map((f) => <option key={f.family_id} value={f.family_id}>{f.name}</option>)}
          </select>
        </label>
      )}
      {binding.status !== "active" && families.length > 0 && (
        <button className="button primary" disabled={action.busy || !fid} onClick={async () => {
          const r = await action.run((key) => api.post<{ code: string; expires_at: string }>("/me/feishu-binding-code", { default_family_id: fid }, { key }));
          if (r) setCode(r as { code: string; expires_at: string });
        }}>生成绑定码</button>
      )}
      {code && binding.status !== "active" && (
        <div className="notice secret-notice">
          <div>
            <strong>在飞书私聊机器人发送：</strong>
            <code>绑定 {code.code}</code>
            <p>10 分钟内有效，只能使用一次。绑定成功后刷新本页查看状态。</p>
            <button className="button secondary small" onClick={() => void reloadMe()}>刷新状态</button>
          </div>
        </div>
      )}
      {binding.status === "active" && (
        <button className="button secondary danger" disabled={action.busy} onClick={async () => {
          if (!confirm("解除飞书绑定？之后飞书消息不会再记入账本。")) return;
          const r = await action.run((key) => api.del("/me/feishu-binding", { key }));
          if (r !== undefined) { setCode(null); await reloadMe(); }
        }}>解除绑定</button>
      )}
      <ErrorNotice error={action.error} />
      <p className="tiny-note">飞书会保留聊天记录副本；在本系统删除照片不会删除飞书中的原消息。</p>
    </section>
  );
}
