import { Alert, App, Button, InputNumber, Select, Space, Switch, Table, Tabs } from "antd";
import { useEffect, useState } from "react";
import { get, opCall } from "../../api";
import { fmtTime } from "../../components/StatusTag";

/** P11 系统设置（F17）。 */
export default function SettingsPage() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [group, setGroup] = useState<string>("文件");
  const load = () => get<any[]>("/api/settings").then((r) => { setRows(r); setEdits({}); });
  useEffect(() => { load(); }, []);
  const groups = [...new Set(rows.map((r) => r.group))];
  const val = (r: any) => (r.key in edits ? edits[r.key] : r.value);
  const setV = (k: string, v: any) => setEdits({ ...edits, [k]: v });
  const control = (r: any) => {
    const v = val(r);
    if (typeof r.default === "boolean") return <Switch checked={!!v} onChange={(x) => setV(r.key, x)} />;
    if (r.key === "guard.driftPolicy") return <Select style={{ width: 140 }} value={v} onChange={(x) => setV(r.key, x)} options={["REJECT", "OVERWRITE"].map((x) => ({ value: x, label: x }))} />;
    const ratio = typeof r.default === "number" && !Number.isInteger(r.default);
    return <InputNumber className="setting-input" data-key={r.key} value={v} min={ratio ? 0 : 1} max={ratio ? 1 : undefined} step={ratio ? 0.05 : 1} precision={ratio ? 2 : 0} onChange={(x) => setV(r.key, x)} />;
  };
  const changed = Object.entries(edits).filter(([k, v]) => rows.find((r) => r.key === k)?.value !== v && v !== null);
  const save = async () => {
    const body = Object.fromEntries(changed);
    const summary = changed.map(([k, v]) => `${k}：${JSON.stringify(rows.find((r) => r.key === k)?.value)} → ${JSON.stringify(v)}`).join("；");
    const r = await opCall({ title: "修改系统设置", summary: summary + "。只影响之后开始执行的任务。" }, "PUT", "/api/settings", () => body)
      .catch((e) => { message.error(e.message); return null; });
    if (r) { message.success("已保存"); setRows(r); setEdits({}); }
  };
  return (
    <div className="cf-page">
      <Alert type="info" showIcon style={{ marginBottom: 8 }} message="修改只影响之后开始执行的任务。" />
      <Tabs activeKey={group} onChange={setGroup} items={groups.map((g) => ({ key: g, label: g }))} />
      <Table rowKey="key" pagination={false} dataSource={rows.filter((r) => r.group === group)} columns={[
        { title: "设置项", dataIndex: "key", render: (v, r: any) => <span className="cf-mono" style={{ borderLeft: r.changed ? "3px solid #1677ff" : undefined, paddingLeft: 6 }}>{v}</span> },
        { title: "说明", dataIndex: "description" },
        { title: "当前值", render: (_: any, r: any) => control(r) },
        { title: "推荐默认", dataIndex: "default", render: (v) => String(v) },
        { title: "最近修改", render: (_: any, r: any) => r.updatedBy ? `${r.updatedBy} · ${fmtTime(r.updatedAt)}` : "—" },
        { title: "", render: (_: any, r: any) => r.changed || r.key in edits ? <a onClick={() => setV(r.key, r.default)}>恢复默认</a> : null },
      ]} />
      <Space style={{ marginTop: 12, float: "right" }}>
        <Button onClick={() => setEdits({})} disabled={!changed.length}>撤销修改</Button>
        <Button type="primary" id="save-settings" disabled={!changed.length} onClick={save}>保存… 🔒</Button>
      </Space>
    </div>
  );
}
