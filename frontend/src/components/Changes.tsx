import { Alert, Select, Space, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import { get } from "../api";

const OP: Record<string, [string, string]> = { c: ["green", "新增"], u: ["blue", "修改"], d: ["red", "删除"] };

function fmt(v: any) {
  if (v === null || v === undefined) return "∅";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** 变更明细（P6「变更明细」、P7 展开行）：按表 + 操作类型筛选；修改行逐字段「旧值 → 新值」。 */
export default function Changes({ url, tables }: { url: string; tables?: string[] }) {
  const [table, setTable] = useState<string | undefined>();
  const [op, setOp] = useState<string | undefined>();
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<any>(null);
  useEffect(() => {
    const qs = new URLSearchParams({ offset: String(offset), limit: "50" });
    if (table) qs.set("table", table);
    if (op) qs.set("op", op);
    get(`${url}?${qs}`).then(setPage).catch(() => setPage(null));
  }, [url, table, op, offset]);
  if (page?.expired) return <Alert type="info" showIcon message="变更明细已超过保留期被清理" />;
  return (
    <div>
      <Space style={{ marginBottom: 6 }}>
        {tables && tables.length > 1 && (
          <Select size="small" allowClear placeholder="表" style={{ width: 200 }} value={table} onChange={(v) => { setTable(v); setOffset(0); }}
            options={tables.map((t) => ({ value: t, label: t }))} />
        )}
        <Select size="small" allowClear placeholder="操作" style={{ width: 100 }} value={op} onChange={(v) => { setOp(v); setOffset(0); }}
          options={Object.entries(OP).map(([k, [, t]]) => ({ value: k, label: t }))} />
      </Space>
      <Table size="small" rowKey={(_, i) => String(offset + (i ?? 0))} dataSource={page?.rows || []}
        pagination={{ current: offset / 50 + 1, pageSize: 50, total: page?.total || 0, onChange: (p) => setOffset((p - 1) * 50) }}
        columns={[
          { title: "表", dataIndex: "table", width: 160 },
          { title: "操作", dataIndex: "op", width: 70, render: (v) => <Tag color={OP[v]?.[0]}>{OP[v]?.[1] || v}</Tag> },
          { title: "主键", dataIndex: "rowKey", width: 180, render: (v) => (v ? <span className="cf-mono">{fmt(v)}</span> : <span className="cf-muted">无主键</span>) },
          {
            title: "内容",
            render: (_: any, r: any) => {
              if (r.op === "u") {
                const fields: string[] = r.changedFields || Object.keys(r.after || {}).filter((k) => fmt(r.after[k]) !== fmt(r.before?.[k]));
                return fields.map((f) => (
                  <div key={f} className="cf-mono">{f}: <span className="cf-err">{fmt(r.before?.[f])}</span> → <span style={{ color: "#389e0d" }}>{fmt(r.after?.[f])}</span></div>
                ));
              }
              return <span className="cf-mono">{fmt(r.after || r.before)}</span>;
            },
          },
        ]} />
      {page?.rows?.some((r: any) => !r.rowKey) && <div className="cf-muted">无主键的表按整行比对，只有新增 / 删除。</div>}
    </div>
  );
}
