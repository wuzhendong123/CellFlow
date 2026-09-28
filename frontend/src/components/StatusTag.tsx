import { Tag } from "antd";

// 状态标签颜色（WIREFRAME §3.3）
const JOB: Record<string, [string, string]> = {
  SUBMITTED: ["blue", "已提交"],
  QUEUED: ["blue", "排队中"],
  RUNNING: ["processing", "执行中"],
  PUBLISHED: ["green", "已写入"],
  NO_CHANGE: ["default", "无变化"],
  SUPERSEDED: ["default", "已作废"],
  VALIDATED: ["success", "校验通过"],
  FAILED_VALIDATION: ["red", "校验失败"],
  FAILED_GUARD: ["orange", "被拦截"],
  FAILED_WRITE: ["red", "写入失败"],
  FAILED: ["red", "系统失败"],
  CANCELLED: ["default", "已取消"],
};

export function JobStatus({ s }: { s: string }) {
  const [c, t] = JOB[s] || ["default", s];
  return <Tag color={c}>{t}</Tag>;
}

const PIPE: Record<string, [string, string]> = {
  NORMAL: ["green", "正常"],
  FROZEN: ["purple", "已冻结"],
  UNPUBLISHED: ["default", "未发布"],
};

export function PipelineStatus({ s }: { s: string }) {
  const [c, t] = PIPE[s] || ["default", s];
  return <Tag color={c}>{t}</Tag>;
}

const KIND: Record<string, [string, string]> = {
  NORMAL: ["blue", "正常"],
  ROLLBACK: ["purple", "回滚"],
  FORCED: ["orange", "人工放行"],
  BASELINE: ["default", "接管基线"],
};

export function ReleaseKind({ s }: { s: string }) {
  const [c, t] = KIND[s] || ["default", s];
  return <Tag color={c}>{t}</Tag>;
}

export function Sev({ s }: { s: string }) {
  return <Tag color={s === "ERROR" ? "red" : s === "WARN" ? "orange" : "default"}>{s}</Tag>;
}

export function fmtTime(v?: string | null) {
  if (!v) return "—";
  const d = new Date(v.replace(" ", "T"));
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return d.toLocaleString("zh-CN", { hour12: false });
}
