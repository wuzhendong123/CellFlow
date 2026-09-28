import { DatePicker, Input, Select, Space, Table, Tabs, Tag } from "antd";
import { useEffect, useRef, useState } from "react";
import { get } from "../api";
import { fmtTime, JobStatus } from "../components/StatusTag";
import { navigate } from "../router";

const STATUS = ["QUEUED", "RUNNING", "PUBLISHED", "NO_CHANGE", "VALIDATED", "FAILED_VALIDATION", "FAILED_GUARD", "FAILED_WRITE", "FAILED", "SUPERSEDED"];
const LABEL: Record<string, string> = {
  QUEUED: "排队中", RUNNING: "执行中", PUBLISHED: "已写入", NO_CHANGE: "无变化", VALIDATED: "校验通过", FAILED_VALIDATION: "校验失败",
  FAILED_GUARD: "被拦截", FAILED_WRITE: "写入失败", FAILED: "系统失败", SUPERSEDED: "已作废", SUBMITTED: "已提交", CANCELLED: "已取消",
};
const MODE: Record<string, string> = { EXECUTE: "写表", VALIDATE_ONLY: "只校验", TEST: "试跑" };

function duration(a?: string | null, b?: string | null) {
  if (!a || !b) return "—";
  const ms = new Date(b).getTime() - new Date(a).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** P5 任务列表（F13）：待处理 / 全部，筛选，状态计数，进行中每 5 秒刷新。 */
export default function Jobs({ query, pipelineId }: { query: URLSearchParams; pipelineId?: number }) {
  const [view, setView] = useState(query.get("view") || "all");
  const [f, setF] = useState<Record<string, any>>({ pipelineId: pipelineId ?? (query.get("pipelineId") ? Number(query.get("pipelineId")) : undefined) });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<any>(null);
  const [pending, setPending] = useState(0);
  const [pipes, setPipes] = useState<any[]>([]);
  const [clients, setClients] = useState<any[]>([]);
  const timer = useRef<any>(null);

  useEffect(() => {
    get<any[]>("/api/pipelines").then(setPipes).catch(() => {});
    get<any[]>("/api/client-apps").then(setClients).catch(() => {});
  }, []);

  const load = () => {
    const qs = new URLSearchParams({ offset: String(offset), limit: "20" });
    for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    if (view === "pending") qs.set("view", "pending");
    get(`/api/jobs?${qs}`).then((d) => {
      setData(d);
      clearTimeout(timer.current);
      if (d.rows.some((r: any) => ["SUBMITTED", "QUEUED", "RUNNING"].includes(r.status))) timer.current = setTimeout(load, 5000);
    });
    const pq = new URLSearchParams({ view: "pending", limit: "1" });
    if (f.pipelineId) pq.set("pipelineId", String(f.pipelineId));
    get(`/api/jobs?${pq}`).then((d) => setPending(d.total)).catch(() => {});
  };
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, [f, view, offset]);
  const set = (k: string, v: any) => { setOffset(0); setF({ ...f, [k]: v }); };
  const counts = data?.statusCounts || {};

  return (
    <div className="cf-page">
      <Tabs activeKey={view} onChange={(k) => { setView(k); setOffset(0); }} items={[
        { key: "pending", label: <span id="tab-pending">待处理 {pending ? <Tag color="orange">{pending}</Tag> : null}</span> },
        { key: "all", label: "全部" },
      ]} />
      <Space wrap style={{ marginBottom: 8 }}>
        {!pipelineId && <Select allowClear showSearch placeholder="方案" style={{ width: 180 }} value={f.pipelineId} onChange={(v) => set("pipelineId", v)}
          options={pipes.map((p) => ({ value: p.id, label: p.code }))} optionFilterProp="label" />}
        <Select allowClear placeholder="调用方" style={{ width: 150 }} value={f.clientAppId} onChange={(v) => set("clientAppId", v)} options={clients.map((c) => ({ value: c.id, label: c.name }))} />
        <Select allowClear placeholder="状态" style={{ width: 130 }} value={f.status} onChange={(v) => set("status", v)} options={STATUS.map((s) => ({ value: s, label: LABEL[s] }))} />
        <Select allowClear placeholder="模式" style={{ width: 110 }} value={f.mode} onChange={(v) => set("mode", v)} options={Object.entries(MODE).map(([k, t]) => ({ value: k, label: t }))} />
        <DatePicker.RangePicker showTime onChange={(v) => { setOffset(0); setF({ ...f, since: v?.[0]?.format("YYYY-MM-DD HH:mm:ss"), until: v?.[1]?.format("YYYY-MM-DD HH:mm:ss") }); }} />
        <Input.Search placeholder="任务 ID" allowClear style={{ width: 140 }} onSearch={(v) => set("jobId", v ? Number(v) : undefined)} />
      </Space>
      <div style={{ marginBottom: 8 }} id="status-counts">
        {STATUS.filter((s) => counts[s]).map((s) => (
          <Tag key={s} style={{ cursor: "pointer" }} color={s === "FAILED_GUARD" ? "orange" : f.status === s ? "blue" : undefined} onClick={() => set("status", f.status === s ? undefined : s)}>
            {LABEL[s]} {counts[s]}
          </Tag>
        ))}
      </div>
      <Table rowKey="jobId" dataSource={data?.rows || []} loading={!data}
        pagination={{ current: offset / 20 + 1, pageSize: 20, total: data?.total || 0, onChange: (p) => setOffset((p - 1) * 20) }}
        onRow={(r) => ({ onClick: () => navigate(`/jobs/${r.jobId}`), style: { cursor: "pointer" } })}
        columns={[
          { title: "任务", dataIndex: "jobId", render: (v) => `#${v}` },
          { title: "方案", dataIndex: "pipelineCode", render: (v) => <span className="cf-mono">{v}</span> },
          { title: "调用方", dataIndex: "client" },
          { title: "模式", dataIndex: "mode", render: (v) => MODE[v] || v },
          { title: "状态", dataIndex: "status", render: (v, r: any) => <Space size={2}><JobStatus s={v} />{r.supersededBy ? <span className="cf-muted">被 #{r.supersededBy} 替代</span> : null}{r.callbackFailed ? <Tag color="orange">回调失败</Tag> : null}</Space> },
          { title: "变更", dataIndex: "changes", render: (c) => (c && c.c + c.u + c.d ? `+${c.c} ~${c.u} -${c.d}` : "—") },
          { title: "问题", render: (_: any, r: any) => <span><span className="cf-err">{r.errors}</span> / <span className="cf-warn">{r.warns}</span></span> },
          { title: "提交时间", dataIndex: "submittedAt", render: fmtTime },
          { title: "耗时", render: (_: any, r: any) => duration(r.submittedAt, r.finishedAt) },
        ]} />
    </div>
  );
}
