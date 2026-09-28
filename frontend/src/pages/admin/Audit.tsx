import { Alert, Input, Select, Space, Table } from "antd";
import { useEffect, useState } from "react";
import { get } from "../../api";

const ACTIONS = ["PUBLISH_REVISION", "REPUBLISH_REVISION", "FORCE_PUBLISH", "ROLLBACK", "FREEZE", "UNFREEZE", "EDIT_DATASOURCE", "EDIT_CLIENT", "EDIT_SETTING"];

/** P12 审计日志（F16）。 */
export default function Audit() {
  const [f, setF] = useState<Record<string, string | undefined>>({});
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    const qs = new URLSearchParams({ offset: String(offset), limit: "50" });
    for (const [k, v] of Object.entries(f)) if (v) qs.set(k, v);
    get(`/api/audit-logs?${qs}`).then(setData);
  }, [f, offset]);
  const set = (k: string, v?: string) => { setOffset(0); setF({ ...f, [k]: v || undefined }); };
  const rows = data?.rows || [];
  return (
    <div className="cf-page">
      <Alert type="info" showIcon style={{ marginBottom: 8 }} message="v1 操作人为自填，接入统一登录后为登录账号。" />
      <Space style={{ marginBottom: 8 }} wrap>
        <Select allowClear placeholder="操作类型" style={{ width: 200 }} value={f.action} onChange={(v) => set("action", v)} options={ACTIONS.map((a) => ({ value: a, label: a }))} />
        <Input.Search allowClear placeholder="对象前缀，如 pipeline:hero" style={{ width: 240 }} onSearch={(v) => set("target", v)} />
        <Input.Search allowClear placeholder="操作人" style={{ width: 160 }} onSearch={(v) => set("operator", v)} />
      </Space>
      <Table rowKey="id" dataSource={rows} size="small"
        pagination={{ current: offset / 50 + 1, pageSize: 50, total: offset + rows.length + (rows.length === 50 ? 1 : 0), onChange: (p) => setOffset((p - 1) * 50) }}
        expandable={{ expandedRowRender: (r: any) => <pre style={{ fontSize: 12, margin: 0 }}>{JSON.stringify(r.detail, null, 2)}</pre> }}
        columns={[
          { title: "时间", dataIndex: "created_at", render: (v) => v?.replace("T", " ").slice(0, 19) },
          { title: "操作人", dataIndex: "operator" }, { title: "来源 IP", dataIndex: "source_ip" },
          { title: "操作", dataIndex: "action" }, { title: "对象", dataIndex: "target" }, { title: "理由", dataIndex: "reason" },
        ]} />
    </div>
  );
}
