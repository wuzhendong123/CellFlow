import { Alert, App, AutoComplete, Button, Card, Checkbox, Drawer, Input, InputNumber, Modal, Radio, Select, Space, Table, Tag, Tooltip } from "antd";
import { useEffect, useState } from "react";
import { get, opCall, post } from "../../api";
import { Dsl, DslNode, outputPorts } from "../../dsl";
import ExprEditor, { insertIntoExpr } from "./ExprEditor";

export interface Analysis {
  ok: boolean;
  errors: any[];
  warnings: any[];
  ports: Record<string, Record<string, { columns: { field: string; type: string; isKey?: boolean }[]; singleRow: boolean }>>;
}

interface Props {
  node: DslNode;
  dsl: Dsl;
  analysis: Analysis | null;
  pipeline: any;
  sampleRows: (nodeId: string, portId: string) => any[];
  onChange: (cfg: any) => void;
  onLabel: (label: string) => void;
  job?: any;
  stale?: boolean;
  running?: boolean;
  onRunUntil?: (nodeId: string) => void;
  onShowAll?: (nodeId: string) => void;
}

export function inputSchema(dsl: Dsl, analysis: Analysis | null, nodeId: string, portId: string) {
  const e = dsl.edges.find((x) => x.target.nodeId === nodeId && x.target.portId === portId);
  if (!e || !analysis) return null;
  return analysis.ports?.[e.source.nodeId]?.[e.source.portId] || null;
}

const fieldsOf = (s: any) => Object.fromEntries((s?.columns || []).map((c: any) => [c.field, c.type]));
const options = (s: any) => (s?.columns || []).map((c: any) => ({ value: c.field, label: `${c.field}（${c.type}）` }));

function paramsCtx(dsl: Dsl, analysis: Analysis | null, node: DslNode) {
  const s = inputSchema(dsl, analysis, node.id, "in_params");
  const out: Record<string, Record<string, string>> = {};
  for (const [alias] of Object.entries(node.config.params || {})) out[alias] = fieldsOf(s);
  return out;
}

/** P3-2 节点配置面板（右侧 E 区）。 */
export default function NodeConfig({ node, dsl, analysis, pipeline, sampleRows, onChange, onLabel, job, stale, running, onRunUntil, onShowAll }: Props) {
  const cfg = node.config;
  const set = (p: any) => onChange({ ...cfg, ...p });
  const errs = (analysis?.errors || []).filter((e) => e.node === node.id);
  const warns = (analysis?.warnings || []).filter((e) => e.node === node.id);
  const mainPort = node.type === "VALIDATOR" ? "in_main" : node.type === "JOIN" ? "in_left" : "in";
  const main = inputSchema(dsl, analysis, node.id, mainPort);
  const paramSchema = inputSchema(dsl, analysis, node.id, "in_params");
  const edge = dsl.edges.find((e) => e.target.nodeId === node.id && e.target.portId === mainPort);
  const rows = edge ? sampleRows(edge.source.nodeId, edge.source.portId) : [];
  // 参数端口的单行样例：表达式预览用真实参数值（否则 params.x 一律为空）
  const paramEdge = dsl.edges.find((e) => e.target.nodeId === node.id && e.target.portId === "in_params");
  const paramRow = paramEdge ? sampleRows(paramEdge.source.nodeId, paramEdge.source.portId)[0] : undefined;
  const pv = paramRow ? Object.fromEntries(Object.keys(cfg.params || {}).map((a) => [a, paramRow])) : undefined;

  const paramBox = (node.type === "FILTER" || node.type === "DERIVE" || node.type === "VALIDATOR") && (
    <Card size="small" title="参数" style={{ marginBottom: 8 }}>
      {paramSchema ? (
        <Space>
          别名 <Input size="small" id="param-alias" style={{ width: 100 }} value={Object.keys(cfg.params || {})[0] || ""}
            onChange={(e) => set({ params: e.target.value ? { [e.target.value]: "in_params" } : {} })} />
          <span className="cf-muted">= 已连接的单行数据（{paramSchema.columns.length} 个字段）</span>
        </Space>
      ) : (
        <span className="cf-muted">可从键值区拖线到节点顶部的虚线圆点（参数端口）</span>
      )}
    </Card>
  );

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Input size="small" value={node.label || ""} placeholder="节点名称" onChange={(e) => onLabel(e.target.value)} />
        <Tag>{node.id}</Tag>
      </Space>
      {errs.map((e, i) => <Alert key={i} type="error" showIcon message={e.message} style={{ marginBottom: 4 }} className="node-error" />)}
      {warns.map((e, i) => <Alert key={"w" + i} type="warning" showIcon message={e.message} style={{ marginBottom: 4 }} />)}
      {main && (
        <FieldPalette columns={main.columns} rows={rows} params={paramsCtx(dsl, analysis, node)} pv={pv}
          insertable={node.type === "FILTER" || node.type === "DERIVE" || node.type === "VALIDATOR"} />
      )}
      {paramBox}
      {node.type === "FILTER" && (
        <Card size="small" title="过滤条件">
          <ExprEditor id="filter-expr" value={cfg.expr || ""} onChange={(v) => set({ expr: v })} ctx={{ fields: fieldsOf(main), params: paramsCtx(dsl, analysis, node), sampleRows: rows, paramValues: pv, expect: "bool" }} />
        </Card>
      )}
      {node.type === "DERIVE" && <DeriveForm cfg={cfg} set={set} main={main} params={paramsCtx(dsl, analysis, node)} rows={rows} pv={pv} />}
      {node.type === "SELECT_RENAME" && <SelectForm cfg={cfg} set={set} main={main} />}
      {node.type === "UNION" && <span className="cf-muted">把多条结构相近的流连到「输入」端口，按字段名对齐，缺失字段为空。</span>}
      {node.type === "PIVOT" && <PivotForm cfg={cfg} set={set} main={main} node={node} dsl={dsl} pipeline={pipeline} />}
      {node.type === "LOOKUP" && <LookupForm cfg={cfg} set={set} main={main} dict={inputSchema(dsl, analysis, node.id, "in_dict")} />}
      {node.type === "JOIN" && <JoinForm cfg={cfg} set={set} left={main} right={inputSchema(dsl, analysis, node.id, "in_right")} />}
      {node.type === "VALIDATOR" && <ValidatorForm node={node} cfg={cfg} set={set} main={main} dsl={dsl} analysis={analysis} params={paramsCtx(dsl, analysis, node)} rows={rows} pv={pv} />}
      {node.type === "SINK" && <SinkForm node={node} cfg={cfg} set={set} main={main} pipeline={pipeline} dsl={dsl} />}
      {paramSchema === null && node.type === "DERIVE" && null}
      {node.type !== "SINK" && onRunUntil && (
        <NodePreview node={node} job={job} stale={stale} running={running} onRun={() => onRunUntil(node.id)} onShowAll={() => onShowAll?.(node.id)} />
      )}
    </div>
  );
}

const sampleText = (vals: any[]) => {
  const xs = vals.filter((v) => v !== null && v !== undefined).slice(0, 3).map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v)));
  return xs.length ? `样例：${xs.join("、")}` : "暂无样例（先预览或试跑）";
};

/** 可用字段面板：输入字段与参数字段（类型 + 样例值），点击插入到最近使用的表达式输入框。 */
function FieldPalette({ columns, rows, params, pv, insertable }: any) {
  const { message } = App.useApp();
  const insert = (text: string) => {
    if (!insertable) return;
    if (!insertIntoExpr(text)) message.info("先点一下要编辑的表达式输入框，再点字段");
  };
  const chip = (text: string, label: string, type: string, tip: string) => (
    <Tooltip key={text} title={<div><div className="cf-mono">{text}</div><div>{tip}</div></div>}>
      <Tag className="field-chip" style={{ cursor: insertable ? "pointer" : "default", marginBottom: 4 }} onClick={() => insert(text)}>
        {label}<span className="cf-muted" style={{ marginLeft: 4 }}>{type}</span>
      </Tag>
    </Tooltip>
  );
  const aliases = Object.entries<Record<string, string>>(params || {});
  return (
    <Card size="small" id="field-palette" style={{ marginBottom: 8 }}
      title={<span>可用字段{insertable && <span className="cf-muted" style={{ fontWeight: 400, marginLeft: 6 }}>点击插入到表达式</span>}</span>}>
      <div>{columns.map((c: any) => chip(c.field, c.field, c.type, sampleText((rows || []).map((r: any) => r?.[c.field]))))}</div>
      {aliases.map(([alias, fields]) => (
        <div key={alias} style={{ marginTop: 4 }}>
          <span className="cf-muted" style={{ marginRight: 4 }}>参数 {alias}：</span>
          {Object.entries(fields).map(([f, t]) => chip(`params.${alias}.${f}`, f, t, sampleText([pv?.[alias]?.[f]])))}
        </div>
      ))}
    </Card>
  );
}

const fmtCell = (v: any) => (v === null || v === undefined ? <span className="cf-muted">∅</span> : typeof v === "object" ? JSON.stringify(v) : String(v));

/** 节点输出预览：显示最近一次试跑中本节点各输出端口的前 5 行，便于确认处理结果。 */
function NodePreview({ node, job, stale, running, onRun, onShowAll }: any) {
  const ports = outputPorts(node);
  const [pages, setPages] = useState<Record<string, any>>({});
  useEffect(() => {
    setPages({});
    if (!job) return;
    for (const p of ports) {
      get(`/api/jobs/${job.jobId}/nodes/${node.id}/ports/${p.id}/rows?offset=0&limit=5`)
        .then((pg) => setPages((m) => ({ ...m, [p.id]: pg })))
        .catch(() => setPages((m) => ({ ...m, [p.id]: null })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.jobId, node.id]);
  const has = ports.some((p) => pages[p.id]);
  return (
    <Card size="small" id="node-preview" title="输出预览" style={{ marginTop: 8 }} extra={
      <Space size={4}>
        {has && <Button size="small" type="link" onClick={onShowAll}>查看全部</Button>}
        <Button size="small" id="preview-until" loading={running} onClick={onRun}>{job ? "重新预览到此节点" : "预览到此节点"}</Button>
      </Space>
    }>
      {stale && job && <Alert type="warning" showIcon style={{ marginBottom: 6 }} message="配置已修改，下面是修改前的结果" />}
      {!job && <span className="cf-muted">点「预览到此节点」，用样例文件（每个区域采样）跑到这里，查看处理结果</span>}
      {job && !has && <span className="cf-muted">本节点在最近一次试跑中没有输出（可能未运行到这里或上游出错），点「预览到此节点」</span>}
      {ports.map((p) => {
        const pg = pages[p.id];
        if (!pg) return null;
        return (
          <div key={p.id} style={{ marginBottom: 6 }}>
            {ports.length > 1 && <div className="cf-muted">{p.label}（{pg.total} 行）</div>}
            {ports.length === 1 && <div className="cf-muted">共 {pg.total} 行，显示前 {pg.rows.length} 行</div>}
            <Table size="small" pagination={false} scroll={{ x: true }} rowKey={(_, i) => String(i)} dataSource={pg.rows}
              columns={(pg.columns || []).map((c: any) => ({ title: c.field, render: (_: any, r: any) => <span className="cf-value">{fmtCell(r.data[c.field])}</span> }))} />
          </div>
        );
      })}
    </Card>
  );
}

function DeriveForm({ cfg, set, main, params, rows, pv }: any) {
  const cols: any[] = cfg.columns || [];
  const upd = (i: number, p: any) => set({ columns: cols.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  const move = (i: number, d: number) => {
    const n = [...cols];
    const [x] = n.splice(i, 1);
    n.splice(i + d, 0, x);
    set({ columns: n });
  };
  const fields = { ...Object.fromEntries((main?.columns || []).map((c: any) => [c.field, c.type])) };
  return (
    <Card size="small" title="派生列（按顺序计算）" extra={<Radio.Group size="small" value={cfg.onError || "ERROR"} onChange={(e) => set({ onError: e.target.value })} options={[{ value: "ERROR", label: "出错报错" }, { value: "NULL", label: "出错置空" }]} />}>
      {cols.map((c, i) => {
        const before = { ...fields };
        cols.slice(0, i).forEach((x) => (before[x.field] = x.type));
        return (
          <Card key={i} size="small" style={{ marginBottom: 6 }} className="derive-col">
            <Space wrap style={{ marginBottom: 4 }}>
              <Input size="small" className="cf-mono derive-field" style={{ width: 110 }} value={c.field} onChange={(e) => upd(i, { field: e.target.value })} />
              <Select size="small" style={{ width: 110 }} value={c.type || "string"} onChange={(v) => upd(i, { type: v })}
                options={["string", "int", "long", "float", "decimal(20,2)", "bool", "date", "datetime"].map((t) => ({ value: t, label: t }))} />
              <Radio.Group size="small" value={c.mode || "ADD"} onChange={(e) => upd(i, { mode: e.target.value })} options={[{ value: "ADD", label: "新增" }, { value: "REPLACE", label: "覆盖" }]} />
              <a onClick={() => i > 0 && move(i, -1)}>↑</a><a onClick={() => i < cols.length - 1 && move(i, 1)}>↓</a>
              <a onClick={() => set({ columns: cols.filter((_, j) => j !== i) })}>删除</a>
            </Space>
            <ExprEditor id={`derive-expr-${i}`} value={c.expr || ""} onChange={(v) => upd(i, { expr: v })} ctx={{ fields: before, params, sampleRows: rows, paramValues: pv }} />
          </Card>
        );
      })}
      <Button size="small" id="add-derive" onClick={() => set({ columns: [...cols, { field: `col${cols.length + 1}`, expr: "", type: "string" }] })}>+ 派生列</Button>
    </Card>
  );
}

function SelectForm({ cfg, set, main }: any) {
  const listed: any[] = cfg.columns || [];
  const all = (main?.columns || []).map((c: any) => c.field);
  const on = (f: string) => listed.find((x) => x.field === f);
  return (
    <Card size="small" title="选列与改名">
      <Table size="small" pagination={false} rowKey="f" dataSource={all.map((f: string) => ({ f }))} columns={[
        { title: "", width: 40, render: (_: any, r: any) => <Checkbox checked={!!on(r.f)} onChange={(e) => set({ columns: e.target.checked ? [...listed, { field: r.f }] : listed.filter((x) => x.field !== r.f) })} /> },
        { title: "字段", dataIndex: "f" },
        { title: "新名称", render: (_: any, r: any) => on(r.f) && <Input size="small" value={on(r.f).as || ""} placeholder={r.f} onChange={(e) => set({ columns: listed.map((x) => (x.field === r.f ? { ...x, as: e.target.value || undefined } : x)) })} /> },
      ]} />
    </Card>
  );
}

function LookupForm({ cfg, set, main, dict }: any) {
  const on = cfg.on?.[0] || {};
  return (
    <Card size="small" title="查表映射">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space>主数据键 <Select size="small" style={{ width: 130 }} value={on.left} options={options(main)} onChange={(v) => set({ on: [{ ...on, left: v }] })} />
          = 字典键 <Select size="small" style={{ width: 130 }} value={on.right} options={options(dict)} onChange={(v) => set({ on: [{ ...on, right: v }] })} /></Space>
        <Space>取回字段 <Select size="small" mode="multiple" style={{ width: 260 }} value={(cfg.select || []).map((s: any) => s.field)} options={options(dict)}
          onChange={(v: string[]) => set({ select: v.map((f) => cfg.select?.find((s: any) => s.field === f) || { field: f }) })} /></Space>
        {(cfg.select || []).map((s: any, i: number) => (
          <Space key={i}>{s.field} 输出为 <Input size="small" style={{ width: 120 }} value={s.as || ""} placeholder={s.field} onChange={(e) => set({ select: cfg.select.map((x: any, j: number) => (j === i ? { ...x, as: e.target.value || undefined } : x)) })} /></Space>
        ))}
        <Space>找不到时 <Select size="small" value={cfg.onMissing || "NULL"} onChange={(v) => set({ onMissing: v })} options={[{ value: "NULL", label: "置空" }, { value: "WARN", label: "置空并警告" }, { value: "ERROR", label: "报错" }]} /></Space>
      </Space>
    </Card>
  );
}

function JoinForm({ cfg, set, left, right }: any) {
  const broadcast = cfg.joinType === "BROADCAST";
  const la = cfg.leftAlias || "left";
  const ra = cfg.rightAlias || "right";
  const aliases = cfg.conflictPolicy?.aliases || {};
  const sel: string[] = cfg.select || [`${la}.*`, `${ra}.*`];
  const lf = (left?.columns || []).map((c: any) => c.field);
  const rf = (right?.columns || []).map((c: any) => c.field);
  const dup = new Set(lf.filter((f: string) => rf.includes(f)));
  const selected = (side: string, f: string) => sel.includes(`${side}.*`) || sel.includes(`${side}.${f}`);
  const toggle = (side: string, f: string, v: boolean) => {
    let s = sel.filter((x) => x !== `${side}.*`);
    const all = side === la ? lf : rf;
    if (sel.includes(`${side}.*`)) s = [...s, ...all.map((x: string) => `${side}.${x}`)];
    s = v ? [...s, `${side}.${f}`] : s.filter((x) => x !== `${side}.${f}`);
    set({ select: Array.from(new Set(s)) });
  };
  return (
    <Card size="small" title="关联">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Radio.Group id="join-type" size="small" value={cfg.joinType} onChange={(e) => set({ joinType: e.target.value })}
          options={[{ value: "INNER", label: "内连接" }, { value: "LEFT", label: "左连接" }, { value: "BROADCAST", label: "广播" }]} />
        {broadcast && <Alert type="info" showIcon message="右侧必须恰好 1 行（如键值区），选中的字段会附加到左侧每一行" />}
        <Space>左别名 <Input size="small" style={{ width: 80 }} value={la} onChange={(e) => set({ leftAlias: e.target.value })} />
          右别名 <Input size="small" style={{ width: 80 }} value={ra} onChange={(e) => set({ rightAlias: e.target.value })} /></Space>
        {!broadcast && (
          <>
            {(cfg.on || []).map((o: any, i: number) => (
              <Space key={i}>
                <Select size="small" style={{ width: 120 }} value={o.left} options={options(left)} onChange={(v) => set({ on: cfg.on.map((x: any, j: number) => (j === i ? { ...x, left: v } : x)) })} /> =
                <Select size="small" style={{ width: 120 }} value={o.right} options={options(right)} onChange={(v) => set({ on: cfg.on.map((x: any, j: number) => (j === i ? { ...x, right: v } : x)) })} />
                <a onClick={() => set({ on: cfg.on.filter((_: any, j: number) => j !== i) })}>删除</a>
              </Space>
            ))}
            <Button size="small" onClick={() => set({ on: [...(cfg.on || []), { left: undefined, right: undefined }] })}>+ 关联键</Button>
            <Checkbox checked={cfg.keyNormalize === "STRING"} onChange={(e) => set({ keyNormalize: e.target.checked ? "STRING" : undefined })}>统一为文本比较（键类型不一致时）</Checkbox>
            <Space>期望对应关系 <Select size="small" style={{ width: 130 }} value={cfg.expectedCardinality || "MANY_TO_ONE"} onChange={(v) => set({ expectedCardinality: v })}
              options={[{ value: "MANY_TO_ONE", label: "多对一" }, { value: "ONE_TO_ONE", label: "一对一" }, { value: "ONE_TO_MANY", label: "一对多" }, { value: "MANY_TO_MANY", label: "多对多" }]} /></Space>
            {cfg.expectedCardinality === "MANY_TO_MANY" && <Alert type="warning" showIcon message="多对多可能让数据量成倍增长，只受绝对行数上限约束" />}
            <Space>未匹配行 <Select size="small" value={cfg.unmatchedPolicy || "SIDE_OUTPUT"} onChange={(v) => set({ unmatchedPolicy: v })} options={[{ value: "SIDE_OUTPUT", label: "进入侧输出" }, { value: "KEEP_NULLS", label: "保留并置空" }]} /></Space>
            <Space>膨胀上限（行） <InputNumber size="small" value={cfg.explosionGuard?.maxOutputRows} placeholder="系统设置" onChange={(v) => set({ explosionGuard: { ...(cfg.explosionGuard || {}), maxOutputRows: v || undefined } })} /></Space>
          </>
        )}
        <Table size="small" pagination={false} rowKey={(r: any) => r.side + r.f} dataSource={[...lf.map((f: string) => ({ side: la, f })), ...rf.map((f: string) => ({ side: ra, f }))]}
          columns={[
            { title: "输出", width: 44, render: (_: any, r: any) => <Checkbox checked={selected(r.side, r.f)} onChange={(e) => toggle(r.side, r.f, e.target.checked)} /> },
            { title: "字段", render: (_: any, r: any) => <span style={{ color: dup.has(r.f) ? "#d46b08" : undefined }}>{r.side}.{r.f}</span> },
            { title: "别名", render: (_: any, r: any) => <Input size="small" value={aliases[`${r.side}.${r.f}`] || ""} placeholder={dup.has(r.f) ? `${r.side}_${r.f}` : r.f}
              onChange={(e) => set({ conflictPolicy: { ...(cfg.conflictPolicy || {}), aliases: { ...aliases, [`${r.side}.${r.f}`]: e.target.value || undefined } } })} /> },
          ]} />
      </Space>
    </Card>
  );
}

const RULES = [
  ["NOT_NULL", "必填"], ["RANGE", "范围"], ["REGEX", "正则"], ["ENUM", "枚举"], ["UNIQUE", "唯一"],
  ["FOREIGN_KEY", "外键"], ["EXPR", "表达式"], ["RECONCILE", "对账"], ["ROW_COUNT", "行数"],
];

function ValidatorForm({ node, cfg, set, main, dsl, analysis, params, rows, pv }: any) {
  const rules: any[] = cfg.rules || [];
  const refs: Record<string, string> = cfg.refs || {};
  const upd = (i: number, p: any) => set({ rules: rules.map((r, j) => (j === i ? { ...r, ...p } : r)) });
  const [newAlias, setNewAlias] = useState("");
  const refSchema = (alias: string) => inputSchema(dsl, analysis, node.id, refs[alias]);
  return (
    <>
      <Card size="small" title="引用输入（外键用）" style={{ marginBottom: 8 }}>
        {Object.entries(refs).map(([a, p]) => (
          <Space key={a} style={{ display: "flex", marginBottom: 4 }}>
            <Tag>{a}</Tag>
            {refSchema(a) ? <span className="cf-muted">已连接（{refSchema(a)!.columns.length} 个字段）</span> : <span className="cf-err">未连线：请从被引用的数据拖线到节点顶部的「{a}」方块端口</span>}
            <a onClick={() => { const n = { ...refs }; delete n[a]; set({ refs: n }); void p; }}>删除</a>
          </Space>
        ))}
        <Space><Input size="small" id="ref-alias" style={{ width: 100 }} placeholder="别名，如 item" value={newAlias} onChange={(e) => setNewAlias(e.target.value)} />
          <Button size="small" id="add-ref" disabled={!/^[A-Za-z_]\w*$/.test(newAlias) || !!refs[newAlias]} onClick={() => { set({ refs: { ...refs, [newAlias]: `in_ref_${newAlias}` } }); setNewAlias(""); }}>+ 添加引用输入</Button></Space>
      </Card>
      <Card size="small" title="规则">
        {rules.map((r, i) => (
          <Card key={i} size="small" style={{ marginBottom: 6 }} title={<Space>
            <Checkbox checked={r.enabled !== false} onChange={(e) => upd(i, { enabled: e.target.checked })} />
            <Tag>{RULES.find((x) => x[0] === r.type)?.[1]}</Tag>
            <Select size="small" value={r.severity || "ERROR"} onChange={(v) => upd(i, { severity: v })} options={[{ value: "ERROR", label: "错误" }, { value: "WARN", label: "警告" }]} />
          </Space>} extra={<a onClick={() => set({ rules: rules.filter((_, j) => j !== i) })}>删除</a>}>
            <RuleParams r={r} upd={(p: any) => upd(i, p)} main={main} refs={refs} refSchema={refSchema} params={params} rows={rows} pv={pv} />
            <Input size="small" style={{ marginTop: 4 }} placeholder="提示文案（可选）" value={r.message || ""} onChange={(e) => upd(i, { message: e.target.value || undefined })} />
          </Card>
        ))}
        <Select id="add-rule" size="small" placeholder="+ 新增规则" style={{ width: 160 }} value={null as any}
          onChange={(t: string) => set({ rules: [...rules, { ruleId: `r${Date.now().toString(36)}`, type: t, severity: "ERROR" }] })}
          options={RULES.map(([v, l]) => ({ value: v, label: l }))} />
      </Card>
    </>
  );
}

function RuleParams({ r, upd, main, refs, refSchema, params, rows, pv }: any) {
  const fields = options(main);
  switch (r.type) {
    case "NOT_NULL":
    case "UNIQUE":
      return <Select size="small" mode="multiple" style={{ width: "100%" }} placeholder="字段" value={r.fields || []} options={fields} onChange={(v) => upd({ fields: v })} />;
    case "RANGE":
      return <Space><Select size="small" style={{ width: 120 }} value={r.field} options={fields} onChange={(v) => upd({ field: v })} />
        最小 <InputNumber size="small" value={r.min} onChange={(v) => upd({ min: v ?? undefined })} /> 最大 <InputNumber size="small" value={r.max} onChange={(v) => upd({ max: v ?? undefined })} /></Space>;
    case "REGEX":
      return <Space><Select size="small" style={{ width: 120 }} value={r.field} options={fields} onChange={(v) => upd({ field: v })} /><Input size="small" className="cf-mono" value={r.pattern || ""} onChange={(e) => upd({ pattern: e.target.value })} /></Space>;
    case "ENUM":
      return <Space><Select size="small" style={{ width: 120 }} value={r.field} options={fields} onChange={(v) => upd({ field: v })} /><Input size="small" placeholder="允许值，逗号分隔" value={(r.values || []).join(",")} onChange={(e) => upd({ values: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Space>;
    case "FOREIGN_KEY": {
      const aliases = Object.keys(refs);
      if (!aliases.length) return <Alert type="warning" showIcon message="请先添加引用输入并连线" />;
      return <Space>
        <Select size="small" style={{ width: 110 }} value={r.field} options={fields} onChange={(v) => upd({ field: v })} /> →
        <Select size="small" style={{ width: 90 }} value={r.ref?.input} options={aliases.map((a) => ({ value: a, label: a }))} onChange={(v) => upd({ ref: { ...(r.ref || {}), input: v } })} /> .
        <Select size="small" style={{ width: 110 }} value={r.ref?.field} options={options(r.ref?.input ? refSchema(r.ref.input) : null)} onChange={(v) => upd({ ref: { ...(r.ref || {}), field: v } })} />
      </Space>;
    }
    case "EXPR":
      return <ExprEditor value={r.expr || ""} onChange={(v) => upd({ expr: v })} ctx={{ fields: Object.fromEntries((main?.columns || []).map((c: any) => [c.field, c.type])), params, sampleRows: rows, paramValues: pv, expect: "bool" }} />;
    case "RECONCILE": {
      const alias = Object.keys(params)[0];
      return <Space wrap>明细 <Input size="small" className="cf-mono" style={{ width: 110 }} placeholder="sum(count)" value={r.detailAgg || ""} onChange={(e) => upd({ detailAgg: e.target.value })} />
        = 汇总 <Select size="small" style={{ width: 130 }} value={r.summary?.field} placeholder={alias ? `${alias} 的字段` : "先连参数端口"} disabled={!alias}
          options={Object.entries(params[alias] || {}).map(([f, t]) => ({ value: f, label: `${f}（${t}）` }))} onChange={(v) => upd({ summary: { param: alias, field: v } })} />
        容差 <InputNumber size="small" style={{ width: 70 }} value={r.tolerance ?? 0} onChange={(v) => upd({ tolerance: v ?? 0 })} /></Space>;
    }
    case "ROW_COUNT":
      return <Space>最少 <InputNumber size="small" value={r.min} onChange={(v) => upd({ min: v ?? undefined })} /> 最多 <InputNumber size="small" value={r.max} onChange={(v) => upd({ max: v ?? undefined })} /></Space>;
  }
  return null;
}

/** 输出节点：数据集名 + 目标表绑定抽屉（P3-3，F7）。 */
function SinkForm({ node, cfg, set, main, pipeline, dsl }: any) {
  const [open, setOpen] = useState(false);
  const b = cfg.binding || {};
  return (
    <Card size="small" title="输出到业务表">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space>数据集名 <Input size="small" id="sink-dataset" className="cf-mono" value={cfg.dataset || ""} onChange={(e) => set({ dataset: e.target.value })} /></Space>
        <Space>目标表 <Tag>{b.table || "未绑定"}</Tag> <Button size="small" id="open-binding" type="primary" onClick={() => setOpen(true)}>绑定目标表</Button></Space>
        {b.keyFields?.length ? <span>主键：{b.keyFields.join(", ")}</span> : <span className="cf-muted">未声明主键：只能整表替换，变更明细只区分新增/删除</span>}
      </Space>
      <BindingDrawer open={open} onClose={() => setOpen(false)} node={node} cfg={cfg} set={set} main={main} pipeline={pipeline} dsl={dsl} />
    </Card>
  );
}

function Opt({ label, children }: { label: string; children: React.ReactNode }) {
  return <span className="cf-opt"><span className="cf-opt-label">{label}</span>{children}</span>;
}

const AGGS = [
  { value: "FIRST", label: "取第一个" }, { value: "SUM", label: "求和" }, { value: "COUNT", label: "计数" },
  { value: "MAX", label: "最大值" }, { value: "MIN", label: "最小值" }, { value: "AVG", label: "平均值" },
];

/** 分组转列（透视）：①分组字段 ②哪个字段的取值变成列 ③列里填哪个字段的值；「从数据生成列」按预览数据的取值自动建列。 */
function PivotForm({ cfg, set, main, node, dsl, pipeline }: any) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const cols: any[] = cfg.columns || [];
  const upd = (i: number, p: any) => set({ columns: cols.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  const generate = async () => {
    const e = dsl.edges.find((x: any) => x.target.nodeId === node.id && x.target.portId === "in");
    if (!e || !cfg.pivotField) return;
    setBusy(true);
    try {
      // 用样例文件预览到上游节点，读出「转列字段」的所有取值
      const j = await post("/api/jobs/test", { pipelineId: pipeline.id, preview: true, untilNodeId: e.source.nodeId, dsl });
      const vals: any[] = await get(`/api/jobs/${j.jobId}/nodes/${e.source.nodeId}/ports/${e.source.portId}/distinct?field=${encodeURIComponent(cfg.pivotField)}`);
      const have = new Set(cols.map((c) => String(c.value)));
      const used = new Set(cols.map((c) => c.field).concat(cfg.groupBy || []));
      const added = vals.filter((v) => !have.has(v.value)).map((v) => {
        let f = v.field, k = 2;
        while (used.has(f)) f = `${v.field}_${k++}`;
        used.add(f);
        return { value: v.value, field: f };
      });
      if (!vals.length) message.warning("预览数据里这个字段没有取值");
      else if (!added.length) message.info("所有取值都已经生成过列了");
      else message.success(`生成了 ${added.length} 列，字段名可以修改；不需要的列直接删除`);
      if (added.length) set({ columns: [...cols, ...added] });
    } catch (err: any) {
      message.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  const opts = options(main);
  return (
    <Card size="small" title="分组转列" id="pivot-form">
      <Space direction="vertical" style={{ width: "100%" }}>
        <div className="cf-muted">例：按 localCurrency 分组，把 item 的每个取值变成一列，格子里填 value → 每个币种一行。</div>
        <Opt label="① 按哪些字段分组"><Select id="pivot-group" size="small" mode="multiple" optionFilterProp="label" style={{ minWidth: 200 }} value={cfg.groupBy || []} options={opts} onChange={(v) => set({ groupBy: v })} /></Opt>
        <Opt label="② 哪个字段的取值变成列"><Select id="pivot-field" size="small" showSearch optionFilterProp="label" style={{ minWidth: 180 }} value={cfg.pivotField} options={opts} onChange={(v) => set({ pivotField: v })} /></Opt>
        <Opt label="③ 列里填哪个字段的值"><Select id="pivot-value" size="small" showSearch optionFilterProp="label" style={{ minWidth: 180 }} value={cfg.valueField} options={opts} onChange={(v) => set({ valueField: v })} /></Opt>
        <Opt label="同一格有多个值时"><Select size="small" style={{ width: 120 }} value={cfg.agg || "FIRST"} options={AGGS} onChange={(v) => set({ agg: v })} /></Opt>
        <Space>
          <Button id="pivot-generate" type="primary" size="small" loading={busy} disabled={!cfg.pivotField} onClick={generate}>从数据生成列</Button>
          <span className="cf-muted">{cols.length ? `已有 ${cols.length} 列` : "先选好 ②，再点这里"}</span>
        </Space>
        {cols.length > 0 && (
          <Table size="small" rowKey={(_, i) => String(i)} pagination={false} dataSource={cols} scroll={{ y: 320 }} columns={[
            { title: `${cfg.pivotField || "取值"}`, dataIndex: "value", ellipsis: true },
            { title: "生成的字段名", dataIndex: "field", width: 170, render: (v, _, i) => <Input size="small" className="cf-mono" value={v} status={/^[A-Za-z_][A-Za-z0-9_]*$/.test(v || "") ? undefined : "error"} onChange={(e) => upd(i, { field: e.target.value.trim() })} /> },
            { title: "", width: 36, render: (_: any, __: any, i: number) => <a onClick={() => set({ columns: cols.filter((_c, j) => j !== i) })}>✕</a> },
          ]} />
        )}
        {cols.length > 0 && <div className="cf-muted">以后的文件里出现了没配置的取值，会给出警告提示（不会静默丢数据），再点一次「从数据生成列」即可补上。</div>}
      </Space>
    </Card>
  );
}

const SQL_TYPES = ["VARCHAR(64)", "VARCHAR(255)", "VARCHAR(1024)", "TEXT", "INT", "BIGINT", "DOUBLE", "DECIMAL(20,2)", "DECIMAL(20,4)", "DECIMAL(20,8)", "TINYINT(1)", "DATE", "DATETIME", "JSON"];

/** 按上游字段新建目标表：推荐列名 / 类型 / 主键，可修改，预览建表语句后创建（需口令）。 */
function CreateTableModal({ pipeline, cfg, node, main, existing, onClose, onCreated }: any) {
  const { message } = App.useApp();
  const snake = (x: string) => (x || "").replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase();
  const [table, setTable] = useState(() => { const t = snake(cfg.dataset || node.label || node.id); return /^[a-z_]/.test(t) ? t : `t_${t}`; });
  const [cols, setCols] = useState<any[]>([]);
  const [pk, setPk] = useState<string[]>([]);
  const [ddl, setDdl] = useState<{ ddl?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  const ds = pipeline.datasourceId;
  useEffect(() => {
    const keys = cfg.binding?.keyFields || (main?.columns || []).filter((c: any) => c.isKey).map((c: any) => c.field);
    post(`/api/datasources/${ds}/tables/propose`, { fields: (main?.columns || []).map((c: any) => ({ field: c.field, type: c.type })), keyFields: keys })
      .then((r) => { setCols(r.columns); setPk(r.primaryKey); }).catch((e) => message.error(e.message));
  }, []);
  useEffect(() => {
    if (!cols.length) return;
    const t = setTimeout(() => post(`/api/datasources/${ds}/tables/ddl`, { table, columns: cols, primaryKey: pk })
      .then((r) => setDdl({ ddl: r.ddl })).catch((e) => setDdl({ error: e.message })), 300);
    return () => clearTimeout(t);
  }, [table, JSON.stringify(cols), JSON.stringify(pk)]);
  const upd = (i: number, p: any) => setCols(cols.map((c, j) => (j === i ? { ...c, ...p } : c)));
  const create = async () => {
    setBusy(true);
    try {
      const r = await opCall({ title: `在业务库新建表 ${table}`, summary: `将在数据源中执行下面的建表语句（只新建，不修改已有表）：\n\n${ddl.ddl}` },
        "POST", `/api/datasources/${ds}/tables`, () => ({ table, columns: cols, primaryKey: pk }));
      if (r) {
        message.success(`已新建表 ${table}，并完成字段映射`);
        onCreated(table, r.table, cols, cols.filter((c) => pk.includes(c.name)).map((c) => c.field));
      }
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open width={860} title="按上游字段新建目标表" onCancel={onClose} okText="创建… 🔒" cancelText="取消"
      okButtonProps={{ id: "do-create-table", loading: busy, disabled: !ddl.ddl || existing.includes(table) }} onOk={create}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Alert type="info" showIcon message="列名由字段名转成下划线形式，类型按字段类型推荐，都可以修改；没有选主键时会自动加一个自增 id 列。只会新建表，不会修改已有的表。" />
        <Space>
          <span>表名</span>
          <Input id="new-table-name" className="cf-mono" style={{ width: 260 }} value={table} onChange={(e) => setTable(e.target.value.trim())}
            status={existing.includes(table) ? "error" : undefined} />
          {existing.includes(table) && <span className="cf-err">已存在同名表</span>}
        </Space>
        <Table size="small" rowKey={(_, i) => String(i)} pagination={false} dataSource={cols} scroll={{ y: 300 }} columns={[
          { title: "来源字段", dataIndex: "field", width: 170, render: (v) => <span className="cf-mono">{v}</span> },
          { title: "列名", dataIndex: "name", width: 200, render: (v, _, i) => <Input size="small" className="cf-mono" value={v} onChange={(e) => upd(i, { name: e.target.value.trim() })} /> },
          { title: "类型", dataIndex: "sqlType", width: 170, render: (v, _, i) => <AutoComplete size="small" style={{ width: 160 }} value={v} options={SQL_TYPES.map((t) => ({ value: t }))} onChange={(x) => upd(i, { sqlType: x })} /> },
          { title: "可空", dataIndex: "nullable", width: 60, render: (v, c: any, i) => <Checkbox checked={v && !pk.includes(c.name)} disabled={pk.includes(c.name)} onChange={(e) => upd(i, { nullable: e.target.checked })} /> },
          { title: "", width: 40, render: (_: any, __: any, i: number) => <a onClick={() => setCols(cols.filter((_c, j) => j !== i))}>✕</a> },
        ]} />
        <Space>
          <span>主键</span>
          <Select mode="multiple" style={{ minWidth: 320 }} placeholder="不选则自动加自增 id" value={pk} onChange={setPk} options={cols.map((c) => ({ value: c.name, label: c.name }))} />
        </Space>
        {ddl.error ? <Alert type="error" showIcon message={ddl.error} /> : ddl.ddl && <pre id="create-table-ddl" className="cf-mono" style={{ fontSize: 12, background: "#fafafa", padding: 8, maxHeight: 220, overflow: "auto", margin: 0 }}>{ddl.ddl}</pre>}
      </Space>
    </Modal>
  );
}

function BindingDrawer({ open, onClose, node, cfg, set, main, pipeline, dsl }: any) {
  const { message } = App.useApp();
  const b = cfg.binding || {};
  const [tables, setTables] = useState<any[]>([]);
  const [desc, setDesc] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const [creating, setCreating] = useState(false);
  const setB = (p: any) => set({ binding: { ...b, ...p } });
  useEffect(() => {
    if (open && pipeline) get<any[]>(`/api/datasources/${pipeline.datasourceId}/tables`).then(setTables).catch((e) => message.error(e.message));
  }, [open, pipeline?.datasourceId]);
  useEffect(() => {
    if (open && b.table && pipeline) get(`/api/datasources/${pipeline.datasourceId}/tables/${b.table}`).then(setDesc).catch(() => setDesc(null));
  }, [open, b.table]);
  const runCheck = () => post(`/api/pipelines/${pipeline.id}/bindings/check`, { nodeId: node.id, dsl: { ...dsl, nodes: dsl.nodes.map((n: any) => (n.id === node.id ? { ...n, config: cfg } : n)) } }).then(setCheck);
  useEffect(() => { if (open && b.table) runCheck().catch(() => {}); }, [open, JSON.stringify(b)]);
  const mapping: any[] = b.columnMapping || [];
  const src = (col: string) => mapping.find((m) => m.column === col)?.field;
  const setMap = (col: string, field?: string) => setB({ columnMapping: [...mapping.filter((m) => m.column !== col), ...(field ? [{ field, column: col }] : [])] });
  const autoMap = (d: any) => {
    const fields = (main?.columns || []).map((c: any) => c.field);
    const norm = (s: string) => s.toLowerCase().replace(/_/g, "");
    setB({ columnMapping: d.columns.map((c: any) => ({ column: c.name, field: fields.find((f: string) => norm(f) === norm(c.name)) })).filter((m: any) => m.field) });
  };
  const unmappedFields = (main?.columns || []).map((c: any) => c.field).filter((f: string) => !mapping.some((m) => m.field === f));
  return (
    <Drawer title="目标表绑定" width={720} open={open} onClose={onClose} destroyOnHidden>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space>
          目标表
          <Select id="binding-table" showSearch style={{ width: 260 }} value={b.table || undefined} placeholder="选择业务表"
            options={tables.map((t) => ({ value: t.table, label: t.owner && t.owner.pipelineId !== pipeline?.id ? `${t.table}（已被 ${t.owner.pipelineCode} 占用）` : t.table, disabled: !!(t.owner && t.owner.pipelineId !== pipeline?.id) }))}
            onChange={async (v) => { setB({ table: v, columnMapping: [] }); const d = await get(`/api/datasources/${pipeline.datasourceId}/tables/${v}`); setDesc(d); autoMap(d); }} />
          <Tooltip title="还没有表？按上游字段自动生成表结构，确认后在业务库里新建">
            <Button id="create-table" disabled={!main?.columns?.length} onClick={() => setCreating(true)}>新建表…</Button>
          </Tooltip>
        </Space>
        {creating && (
          <CreateTableModal pipeline={pipeline} cfg={cfg} node={node} main={main} existing={tables.map((t) => t.table)} onClose={() => setCreating(false)}
            onCreated={(table: string, d: any, cols: any[], pkFields: string[]) => {
              setCreating(false);
              setTables([...tables, { table }]);
              setDesc(d);
              setB({ table, columnMapping: cols.filter((c) => c.field).map((c) => ({ field: c.field, column: c.name })), keyFields: pkFields.length ? pkFields : null });
            }} />
        )}
        {check && (
          <>
            {check.errors.map((e: any, i: number) => <Alert key={i} type="error" showIcon message={e.message} className="binding-error" />)}
            {check.warnings.map((e: any, i: number) => <Alert key={"w" + i} type={e.code === "TAKEOVER_BASELINE" ? "info" : "warning"} showIcon message={e.message}
              action={e.code === "KEY_SUGGESTED" && check.keySuggestion ? <Button size="small" onClick={() => setB({ keyFields: check.keySuggestion })}>采用</Button> : undefined} />)}
            {check.ok && <Alert type="success" showIcon message="可以整表替换" />}
          </>
        )}
        {desc && (
          <Table size="small" rowKey="name" pagination={false} dataSource={desc.columns} columns={[
            { title: "列名", dataIndex: "name", render: (v, c: any) => <span className="cf-mono" style={{ color: !c.nullable && !c.hasDefault && !src(v) ? "#cf1322" : undefined }}>{v}{c.autoIncrement ? "（自增）" : ""}</span> },
            { title: "类型", dataIndex: "columnType" },
            { title: "可空", dataIndex: "nullable", render: (v) => (v ? "是" : "否") },
            { title: "默认值", dataIndex: "dflt", render: (v) => v ?? "" },
            { title: "映射来源", render: (_: any, c: any) => <Select size="small" allowClear style={{ width: 160 }} value={src(c.name)} options={options(main)} onChange={(v) => setMap(c.name, v)} /> },
          ]} />
        )}
        {unmappedFields.length > 0 && <span className="cf-muted">未被映射的字段：{unmappedFields.join("、")}</span>}
        <Space>主键（可选）<Select size="small" mode="multiple" style={{ width: 260 }} value={b.keyFields || []} options={options(main)} onChange={(v) => setB({ keyFields: v.length ? v : null })} /></Space>
        <Space>写入方式 <Tag>整表替换（v1）</Tag></Space>
        <Space wrap>安全闸覆盖（空为系统设置）
          删除比例 <InputNumber size="small" min={0} max={1} step={0.05} value={b.guards?.maxDeleteRatio} onChange={(v) => setB({ guards: { ...(b.guards || {}), maxDeleteRatio: v ?? undefined } })} />
          行数波动 <InputNumber size="small" min={0} max={1} step={0.05} value={b.guards?.maxRowChangeRatio} onChange={(v) => setB({ guards: { ...(b.guards || {}), maxRowChangeRatio: v ?? undefined } })} />
          <Checkbox checked={b.guards?.forbidEmpty ?? true} onChange={(e) => setB({ guards: { ...(b.guards || {}), forbidEmpty: e.target.checked } })}>清空保护</Checkbox>
        </Space>
        <Button onClick={() => runCheck().catch((e: any) => message.error(e.message))}>重新检查</Button>
      </Space>
    </Drawer>
  );
}
