import { api, type Schemas } from "../api/client";
import { sha256Hex } from "./format";

export const REJECT_REASON: Record<string, string> = {
  format_mismatch: "文件内容与格式不符",
  too_large: "照片超过 10 MB",
  too_many_pixels: "照片像素过大",
  digest_mismatch: "上传内容校验失败",
  unreadable: "无法读取该图片",
};

export type UploadResult = { ok: true; id: string } | { ok: false; reason: string };

/** 上传意图 → 浏览器直传 → 服务端校验真实格式、像素与摘要。 */
export async function uploadPhoto(fid: string, file: File): Promise<UploadResult> {
  const heic = file.type === "image/heic" || file.name.toLowerCase().endsWith(".heic");
  if (heic || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    return { ok: false, reason: "仅支持 JPEG／PNG／WebP，HEIC 请改为 JPEG" };
  }
  try {
    const buf = await file.arrayBuffer();
    const intent = await api.post<Schemas["UploadIntent"]>(`/families/${fid}/attachments/upload-intents`, {
      content_type: file.type, bytes: file.size, sha256: await sha256Hex(buf),
    });
    const form = new FormData();
    Object.entries(intent.fields).forEach(([k, v]) => form.append(k, v));
    form.append("file", new Blob([buf], { type: file.type }), file.name);
    const up = await fetch(intent.upload_url, { method: "POST", body: form });
    if (!up.ok && up.status !== 204) return { ok: false, reason: "上传失败" };
    const done = await api.post<Schemas["Attachment"]>(`/families/${fid}/attachments/${intent.attachment_id}/complete`);
    return done.status === "ready" ? { ok: true, id: done.attachment_id } : { ok: false, reason: REJECT_REASON[done.reject_reason ?? ""] ?? "照片未通过校验" };
  } catch (e) {
    return { ok: false, reason: e instanceof Error ? e.message : "上传失败" };
  }
}
