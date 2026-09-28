import { Alert, Button, Empty, Select, Space, Table, Tabs, Tag } from "antd";
import { useEffect, useMemo, useState } from "react";
import { get } from "../../api";
import { Sev } from "../../components/StatusTag";
import { Dsl, outputPorts } from "../../dsl";

interface Props {
  job: any | null;
  issues: any[];
  dsl: Dsl;
  selectedNode: string | null;
  focusData?: { node: string; nonce: number } | null;
  regionPreview: any | null;
  collapsed: boolean;
  onToggle: () => void;
  onIssue: (i: any) => void;
  onCell: (addr: string) => void;
}

/** P3-4 试跑结果面板（F6-7、F8）。 */
export default function ResultPanel({ job, issues, dsl, selectedNode, focusData, regionPreview, collapsed, onToggle, onIssue, onCell }: Props) {
  const [tab, setTab] = useState("data");
  const [target, setTarget] = useState<string | null>(null);
  const [page, setPage] = useState<any>(null);
  const [offset, setOffset] = useState(0);
  const [sev, setSev] = useState<string | undefined>();
  const [nodeF, setNodeF] = useState<string | undefined>();

  const ports = useMemo(
    () => dsl.nodes.flatMap((n) => outputPorts(n).map((p) => ({ value: `${n.id}|${p.id}`, label: `${n.label || n.id} → ${p.label}${p.side ? "（侧输出）" : ""}` }))),
    [dsl],
  );
  // 选中节点时，数据页签切到该节点的第一个输出端口
  const showNode = (nodeId: string) => {
    const first = ports.find((p) => p.value.startsWith(nodeId + "|"));
    if (!first) return;
    setTarget(first.value);
    setOffset(0);
    if (job) setTab("data");
  };
  useEffect(() => { if (selectedNode) showNode(selectedNode); }, [selectedNode]);
  useEffect(() => { if (focusData) showNode(focusData.node); }, [focusData?.nonce]);
  useEffect(() => { if (regionPreview) setTab("region"); }, [regionPreview]);
  useEffect(() => {
    if (!job || !target) { setPage(null); return; }
    const [n, p] = target.split("|");
    get(`/api/jobs/${job.jobId}/nodes/${n}/ports/${p}/rows?offset=${offset}&limit=50`).then(setPage).catch(() => setPage(null));
  }, [job?.jobId, target, offset]);

  const shown = issues.filter((i) => (!sev || i.severity === sev) && (!nodeF || i.node === nodeF));
  const counts = { e: issues.filter((i) => i.severity === "ERROR").length, w: issues.filter((i) => i.severity === "WARN").length };

  const dataTable = (pg: any, sideLabel?: boolean) => pg ? (
    <Table size="small" rowKey={(r: any, i) => r._rid || String(i)} scroll={{ x: true }} pagination={{ current: Math.floor((pg.offset || 0) / 50) + 1, pageSize: 50, total: pg.total, onChange: (p) => setOffset((p - 1) * 50) }}
      dataSource={pg.rows} columns={(pg.columns || []).map((c: any) => ({
        title: <span>{c.field}<span className="cf-muted"> {c.type}</span></span>,
        render: (_: any, r: any) => {
          const v = r.data[c.field];
          const src = r._lineage?.[c.field];
          const addr = Array.isArray(src) ? src[0] : src;
          return <a className="cf-value" onClick={() => addr && onCell(addr)} title={addr || ""} style={{ color: "inherit" }}>{v === null || v === undefined ? <span className="cf-muted">∅</span> : typeof v === "object" ? JSON.stringify(v) : String(v)}</a>;
        },
      }))} />
  ) : <Empty description={sideLabel ? "暂无数据" : "先试跑，再选择节点与端口"} />;

  const items = [
    { key: "data", label: "数据", children: (
      <div>
        <Space style={{ marginBottom: 6 }}><Select size="small" style={{ width: 360 }} value={target} onChange={(v) => { setTarget(v); setOffset(0); }} options={ports} placeholder="节点 → 端口" /></Space>
        {dataTable(page)}
      </div>
    ) },
    { key: "issues", label: <span id="tab-issues">问题（<span className="cf-err">{counts.e}</span>/<span className="cf-warn">{counts.w}</span>）</span>, children: (
      <div>
        <Space style={{ marginBottom: 6 }}>
          <Select size="small" allowClear placeholder="级别" style={{ width: 100 }} value={sev} onChange={setSev} options={[{ value: "ERROR", label: "ERROR" }, { value: "WARN", label: "WARN" }, { value: "INFO", label: "INFO" }]} />
          <Select size="small" allowClear placeholder="节点" style={{ width: 160 }} value={nodeF} onChange={setNodeF} options={dsl.nodes.map((n) => ({ value: n.id, label: n.label || n.id }))} />
        </Space>
        <Table size="small" rowKey="id" dataSource={shown} pagination={{ pageSize: 20 }}
          onRow={(r) => ({ onClick: () => onIssue(r), style: { cursor: "pointer" } })}
          columns={[
            { title: "级别", dataIndex: "severity", width: 80, render: (v) => <Sev s={v} /> },
            { title: "节点", dataIndex: "node", width: 120 },
            { title: "规则", dataIndex: "rule", width: 70 },
            { title: "位置", width: 130, render: (_: any, r: any) => r.cell ? <span className="cf-mono">{r.sheet}!{r.cell}</span> : "" },
            { title: "字段", dataIndex: "field", width: 90 },
            { title: "值", dataIndex: "value", width: 90, ellipsis: true },
            { title: "说明", dataIndex: "message", className: "issue-message" },
          ]} />
      </div>
    ) },
    { key: "locate", label: "定位报告", children: (
      <Table size="small" rowKey={(r: any) => r.node + r.regionId + (r.sheet || "")} pagination={false} dataSource={job?.metrics?.locateReport || []} columns={[
        { title: "节点", dataIndex: "node" }, { title: "区域", dataIndex: "name" }, { title: "Sheet", dataIndex: "sheet" },
        { title: "设计时范围", render: (_: any, r: any) => r.designRange?.a1 || "" },
        { title: "本次范围", render: (_: any, r: any) => r.range?.a1 },
        { title: "偏移", render: (_: any, r: any) => r.offset ? `行 ${r.offset.rows >= 0 ? "+" : ""}${r.offset.rows}，行数 ${r.offset.heightChange >= 0 ? "+" : ""}${r.offset.heightChange}` : "" },
      ]} />
    ) },
    { key: "forecast", label: "变更摘要", children: (() => {
      const fc = job?.result?.forecast;
      if (!fc) return <Empty description={job?.result?.sampled ? "预览模式不做变更预判，请完整试跑" : "完整试跑后显示"} />;
      if (fc.error) return <Alert type="warning" showIcon message={`无法预判：${fc.error}`} />;
      return <Table size="small" rowKey="table" pagination={false} dataSource={fc.tables} columns={[
        { title: "目标表", dataIndex: "table" }, { title: "行数", dataIndex: "rows" },
        { title: "新增/修改/删除", render: (_: any, r: any) => `${r.changes.c} / ${r.changes.u} / ${r.changes.d}` },
        { title: "安全闸预判", render: (_: any, r: any) => r.blockedBy.length ? r.blockedBy.map((g: string) => <Tag key={g} color="orange">{g}：{r.guards.find((x: any) => x.guard === g)?.message}</Tag>) : <Tag color="green">可写入</Tag> },
      ]} />;
    })() },
    { key: "region", label: "区域输出", children: regionPreview ? (
      <div>
        {(regionPreview.issues || []).map((i: any, k: number) => <Alert key={k} type={i.severity === "ERROR" ? "error" : "warning"} showIcon message={`${i.code}：${i.message}`} style={{ marginBottom: 4 }} />)}
        {regionPreview.locateReport?.[0] && <div className="cf-muted">本次定位：{regionPreview.locateReport[0].range.a1}</div>}
        {dataTable(regionPreview.output, true)}
      </div>
    ) : <Empty description="在区域配置中点击「预览本区域」" /> },
  ];
  return (
    <div className={`cf-result ${collapsed ? "collapsed" : ""}`}>
      <div style={{ display: "flex", alignItems: "center", padding: "4px 8px", borderBottom: "1px solid #f0f0f0", gap: 8 }}>
        <Button size="small" type="text" onClick={onToggle}>{collapsed ? "▲ 结果面板" : "▼ 收起"}</Button>
        {job && <span className="cf-muted">任务 #{job.jobId}，{job.result?.sampled ? <Tag color="gold">基于采样（每区域 {job.result.sampleRows} 行，被引用数据不采样）</Tag> : "完整试跑"}</span>}
      </div>
      {!collapsed && <Tabs size="small" activeKey={tab} onChange={setTab} items={items} style={{ padding: "0 8px" }} />}
    </div>
  );
}
