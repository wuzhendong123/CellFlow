import { Alert, App, Button, Descriptions, Empty, Modal, Result, Select, Space, Spin, Steps, Table, Tabs, Tag } from "antd";
import { lazy, Suspense, useEffect, useState } from "react";
import { get, opCall } from "../api";
import Changes from "../components/Changes";
import { fmtTime, JobStatus, Sev } from "../components/StatusTag";
import { navigate } from "../router";

const SheetPane = lazy(() => import("./workspace/SheetPane"));

const MODE: Record<string, string> = { EXECUTE: "写表", VALIDATE_ONLY: "只校验", TEST: "试跑" };
const NON_OVERRIDABLE = ["G1", "G6", "G8"];

function stages(j: any): { items: any[]; current: number; failed: boolean } {
  const names = ["提交", "排队", "解析", "校验", "安全闸", "写入"];
  const s = j.status;
  let reached = 5;
  let failed = false;
  if (["SUBMITTED"].includes(s)) reached = 0;
  else if (s === "QUEUED") reached = 1;
  else if (s === "RUNNING") reached = 2;
  else if (s === "FAILED_VALIDATION") { reached = 3; failed = true; }
  else if (s === "FAILED_GUARD") { reached = 4; failed = true; }
  else if (s === "FAILED_WRITE") { reached = 5; failed = true; }
  else if (s === "FAILED") { reached = j.startedAt ? 2 : 1; failed = true; }
  else if (s === "VALIDATED" || (j.mode !== "EXECUTE" && s !== "SUPERSEDED")) reached = 3;
  else if (s === "SUPERSEDED") reached = 4;
  const items = names.slice(0, j.mode === "EXECUTE" ? 6 : 4).map((t, i) => ({
    title: t,
    description: i === 0 ? fmtTime(j.submittedAt) : i === reached && failed ? <span className="cf-err">{j.issues?.message || "失败"}</span> : undefined,
  }));
  return { items, current: Math.min(reached, items.length - 1), failed };
}

/** P6 任务详情（F10~F13）。 */
export default function JobDetail({ id }: { id: number }) {
  const { message } = App.useApp();
  const [j, setJ] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [issues, setIssues] = useState<any>(null);
  const [sev, setSev] = useState<string | undefined>();
  const [offset, setOffset] = useState(0);
  const [callbacks, setCallbacks] = useState<any[]>([]);
  const [file, setFile] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);

  const load = () => get(`/api/jobs/${id}`).then(setJ).catch((e) => setErr(e.message));
  useEffect(() => { load(); get(`/api/jobs/${id}/callbacks`).then(setCallbacks).catch(() => {}); }, [id]);
  useEffect(() => { if (j?.fileId) get(`/api/files/${j.fileId}`).then(setFile).catch(() => {}); }, [j?.fileId]);
  useEffect(() => {
    if (!j || ["SUBMITTED", "QUEUED", "RUNNING"].includes(j.status)) {
      const t = setTimeout(load, 3000);
      return () => clearTimeout(t);
    }
  }, [j?.status]);
  useEffect(() => {
    const qs = new URLSearchParams({ offset: String(offset), limit: "100" });
    if (sev) qs.set("severity", sev);
    get(`/api/jobs/${id}/issues?${qs}`).then(setIssues).catch(() => {});
  }, [id, sev, offset, j?.status]);

  if (err) return <Result status="404" title="任务不存在" subTitle={err} extra={<Button onClick={() => navigate("/jobs")}>返回任务列表</Button>} />;
  if (!j) return <Spin style={{ margin: 48 }} />;
  const guards: any[] = j.result?.guards || [];
  const blocked: string[] = j.result?.blockedBy || [];
  const canForce = j.status === "FAILED_GUARD" && blocked.length > 0 && blocked.every((g) => !NON_OVERRIDABLE.includes(g));
  const st = stages(j);

  const force = async () => {
    const failed = guards.filter((g) => !g.passed).map((g) => `${g.guard} ${g.message}`).join("；");
    const r = await opCall({ title: `放行任务 #${j.jobId}`, reasonRequired: true, summary: `将忽略安全闸（${failed}），把本任务结果写入业务表，生成「人工放行」发布。` },
      "POST", `/api/jobs/${j.jobId}/force-publish`).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success(`已放行，发布 #${r.releaseId}`); load(); }
  };

  const tabs = [
    { key: "issues", label: `问题（${j.issues?.error ?? 0} / ${j.issues?.warn ?? 0}）`, children: (
      <div>
        {j.issues?.code && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={`${j.issues.code}：${j.issues.message}`} />}
        <Select size="small" allowClear placeholder="级别" style={{ width: 100, marginBottom: 6 }} value={sev} onChange={(v) => { setSev(v); setOffset(0); }}
          options={["ERROR", "WARN", "INFO"].map((s) => ({ value: s, label: s }))} />
        <Table size="small" rowKey="id" dataSource={issues?.rows || []} id="job-issues"
          pagination={{ current: offset / 100 + 1, pageSize: 100, total: issues?.total || 0, onChange: (p) => setOffset((p - 1) * 100) }}
          onRow={(r) => ({ onClick: () => r.sheet && r.cell && setPreview(r), style: { cursor: r.cell ? "pointer" : undefined } })}
          columns={[
            { title: "级别", dataIndex: "severity", width: 80, render: (v) => <Sev s={v} /> },
            { title: "节点", dataIndex: "node", width: 110 },
            { title: "规则", dataIndex: "rule", width: 70 },
            { title: "位置", width: 140, render: (_: any, r: any) => r.cell ? <a className="cf-mono">{r.sheet}!{r.cell}</a> : "" },
            { title: "字段", dataIndex: "field", width: 100 },
            { title: "值", dataIndex: "value", width: 100, ellipsis: true },
            { title: "说明", dataIndex: "message" },
          ]} />
      </div>
    ) },
    { key: "changes", label: "变更明细", children: j.releaseId ? <Changes url={`/api/jobs/${j.jobId}/changes`} tables={(j.result?.tables || []).map((t: any) => t.table)} /> : <Empty description={j.status === "NO_CHANGE" ? "内容与线上相同，没有写入" : "本任务没有写入业务表"} /> },
    { key: "guards", label: "安全闸结果", children: guards.length ? (
      <Table size="small" rowKey={(g: any) => g.guard + g.table} pagination={false} dataSource={guards} id="guard-table" columns={[
        { title: "闸", dataIndex: "guard", width: 60 }, { title: "名称", dataIndex: "name" }, { title: "表", dataIndex: "table" },
        { title: "结果", dataIndex: "passed", render: (v, g: any) => v ? <Tag color="green">通过</Tag> : <Tag color={g.overridable ? "orange" : "red"}>未通过{g.overridable ? "" : "（不可放行）"}</Tag> },
        { title: "实际 / 阈值", render: (_: any, g: any) => g.actual != null ? `${fmtNum(g.actual)} / ${fmtNum(g.threshold)}` : "" },
        { title: "说明", dataIndex: "message" },
      ]} />
    ) : j.result?.forecast ? (
      <Table size="small" rowKey="table" pagination={false} dataSource={j.result.forecast.tables || []} columns={[
        { title: "目标表", dataIndex: "table" }, { title: "预计变更", render: (_: any, r: any) => `+${r.changes.c} ~${r.changes.u} -${r.changes.d}` },
        { title: "安全闸预判", render: (_: any, r: any) => r.blockedBy?.length ? r.blockedBy.map((g: string) => <Tag key={g} color="orange">{g}</Tag>) : <Tag color="green">可写入</Tag> },
      ]} />
    ) : <Empty description="未执行到安全闸阶段" /> },
    { key: "callback", label: "回调", children: callbacks.length ? (
      <Table size="small" rowKey="id" pagination={false} dataSource={callbacks} columns={[
        { title: "回调地址", dataIndex: "target" }, { title: "状态", dataIndex: "status", render: (v) => <Tag color={v === "DELIVERED" ? "green" : v === "FAILED" ? "red" : "blue"}>{v}</Tag> },
        { title: "尝试次数", dataIndex: "attempts" }, { title: "最近错误", dataIndex: "lastError", ellipsis: true }, { title: "下次重试", dataIndex: "nextRetryAt", render: fmtTime },
      ]} />
    ) : <Empty description="本任务没有回调" /> },
    { key: "file", label: "文件与指标", children: (
      <div>
        <Descriptions size="small" column={2} items={[
          { key: "n", label: "文件", children: <a href={`/api/jobs/${j.jobId}/file`}>{file?.fileName || "下载原始文件"}</a> },
          { key: "s", label: "sha256", children: <span className="cf-mono" style={{ fontSize: 11 }}>{file?.sha256 || "—"}</span> },
        ]} />
        <Table size="small" rowKey="node" pagination={false} style={{ marginTop: 8 }}
          dataSource={Object.entries<any>(j.metrics?.nodes || {}).map(([node, m]) => ({ node, ...m }))}
          columns={[
            { title: "节点", dataIndex: "node" },
            { title: "输出行数", render: (_: any, m: any) => m.failed ? <span className="cf-err">失败</span> : Object.entries<any>(m.rows || {}).map(([p, n]) => `${p}: ${n}`).join("，") },
            { title: "耗时", dataIndex: "ms", render: (v) => `${v} ms` },
          ]} />
        {j.metrics?.skipped?.length ? <Alert style={{ marginTop: 8 }} type="warning" showIcon message={`因上游失败被跳过的节点：${j.metrics.skipped.join("，")}`} /> : null}
        <h4 style={{ marginTop: 12 }}>定位报告</h4>
        <Table size="small" rowKey={(r: any) => r.node + r.regionId + (r.sheet || "")} pagination={false} dataSource={j.metrics?.locateReport || []} columns={[
          { title: "节点", dataIndex: "node" }, { title: "区域", dataIndex: "name" }, { title: "Sheet", dataIndex: "sheet" },
          { title: "设计时", render: (_: any, r: any) => r.designRange?.a1 || "" }, { title: "本次", render: (_: any, r: any) => r.range?.a1 },
        ]} />
      </div>
    ) },
  ];

  return (
    <div className="cf-page">
      <div className="cf-crumb" style={{ margin: "-16px -16px 12px" }}><a onClick={() => navigate("/jobs")}>任务</a> / #{j.jobId}</div>
      <Space align="center" size="large" wrap>
        <h2 style={{ margin: 0 }}>任务 #{j.jobId}</h2>
        <JobStatus s={j.status} />
        <span>方案 <a className="cf-mono" onClick={() => navigate(`/pipelines/${j.pipelineId}/canvas`)}>{j.pipelineCode}</a>{j.rev ? ` v${j.rev}` : j.mode === "TEST" ? "（草稿）" : ""}</span>
        <span>模式：{MODE[j.mode] || j.mode}</span>
        <span>提交人：{j.operator || "—"}</span>
        <span className="cf-muted">提交 {fmtTime(j.submittedAt)} · 完成 {fmtTime(j.finishedAt)}</span>
        {canForce && <Button danger id="force-publish" onClick={force}>放行 🔒</Button>}
        {j.releaseId && <Button onClick={() => navigate(`/pipelines/${j.pipelineId}/releases`)}>查看对应发布 #{j.releaseId}</Button>}
      </Space>
      {j.status === "FAILED_GUARD" && !canForce && <Alert style={{ marginTop: 8 }} type="error" showIcon message="被不可放行的安全闸拦截（ERROR 级问题 / 表结构不兼容 / 值超出列定义），请修正文件或方案后重新提交" />}
      {j.supersededBy && <Alert style={{ marginTop: 8 }} type="info" showIcon message={<span>本任务已作废：被 <a onClick={() => navigate(`/jobs/${j.supersededBy}`)}>#{j.supersededBy}</a> 替代</span>} />}
      <Steps size="small" style={{ margin: "16px 0" }} current={st.current} status={st.failed ? "error" : ["SUBMITTED", "QUEUED", "RUNNING"].includes(j.status) ? "process" : "finish"} items={st.items} />
      <Tabs items={tabs} />
      <Modal open={!!preview} width="90vw" footer={null} title={preview ? `${preview.sheet}!${preview.cell}：${preview.message}` : ""} onCancel={() => setPreview(null)} destroyOnHidden>
        {preview && (
          <div style={{ height: "70vh", display: "flex" }}>
            <Suspense fallback={<Spin />}><SheetPane fileId={j.fileId} sheets={[preview.sheet]} sheet={preview.sheet} onSheet={() => {}} overlays={[]} highlight={{ cell: preview.cell, nonce: Date.now() }} onSelection={() => {}} /></Suspense>
          </div>
        )}
      </Modal>
    </div>
  );
}

function fmtNum(v: any) {
  return typeof v === "number" && v > 0 && v < 1 ? `${(v * 100).toFixed(1)}%` : String(v);
}
