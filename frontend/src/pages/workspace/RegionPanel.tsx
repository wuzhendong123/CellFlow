import { App, Button, Card, Checkbox, Collapse, Input, InputNumber, List, Popconfirm, Radio, Select, Space, Table, Tag, Tooltip } from "antd";
import { useState } from "react";
import { post } from "../../api";
import { DslNode, newId, parseA1, RangeJson, REGION_COLORS, SHAPES, toA1 } from "../../dsl";

const TYPES = ["string", "int", "long", "float", "decimal(20,2)", "bool", "date", "datetime", "enum", "json", "list<string>", "list<long>"];

interface Props {
  node: DslNode;
  fileId: number | null;
  sheets: string[];
  selection: RangeJson | null;
  issues: any[];
  onChange: (cfg: any) => void;
  onFocus: (r: RangeJson) => void;
  onPreview: (region: any) => void;
}

/** P3-1 区域配置（分屏右侧）：区域列表 + 三步配置（形态 → 定位方式 → 字段）。 */
export default function RegionPanel({ node, fileId, sheets, selection, issues, onChange, onFocus, onPreview }: Props) {
  const { message } = App.useApp();
  const cfg = node.config;
  const regions: any[] = cfg.regions || [];
  const [active, setActive] = useState<string | null>(regions[0]?.regionId || null);
  const region = regions.find((r) => r.regionId === active) || null;
  const sheetRule = cfg.sheet || { match: "EXACT", value: "" };

  const setRegions = (rs: any[]) => onChange({ ...cfg, regions: rs });
  const upd = (patch: any) => setRegions(regions.map((r) => (r.regionId === active ? { ...r, ...patch } : r)));
  const updOpt = (patch: any) => upd({ shapeOptions: { ...(region?.shapeOptions || {}), ...patch } });

  const currentSheet = () => (sheetRule.match === "EXACT" ? sheetRule.value : sheets[0]);

  const buildRegion = (sug: any, range: RangeJson, name: string, rid: string, port: string) => ({
    regionId: rid, name, shape: sug.shape, outputPortId: port,
    designRange: { ...range, a1: toA1(range) }, locator: sug.locator,
    shapeOptions: sug.shape === "DETAIL" ? { headerRows: 1 } : sug.shape === "KEY_VALUE" ? { keyCol: 1, valueCol: 2 } : sug.shape === "MATRIX" ? { rowHeaderCols: 1, colHeaderRows: 1, rowDims: ["row"], colDims: ["col"], valueName: "value", dropEmpty: true } : {},
    columns: sug.columns || [],
    recommended: { shape: sug.shape, locator: sug.locator?.type },
  });

  const createFromSelection = async () => {
    if (!selection || !fileId) return;
    let sug: any = { shape: "DETAIL", locator: { type: "FIXED" }, columns: [] };
    try {
      sug = await post("/api/regions/suggest", { fileId, sheet: currentSheet(), range: selection });
    } catch (e: any) {
      message.warning("无法推荐形态：" + e.message);
    }
    const rid = newId("rg", regions.map((r) => r.regionId));
    const port = newId("out", regions.map((r) => r.outputPortId));
    setRegions([...regions, buildRegion(sug, selection, `区域${regions.length + 1}`, rid, port)]);
    setActive(rid);
  };

  const [detecting, setDetecting] = useState(false);
  const autoDetect = async () => {
    const sheet = currentSheet();
    if (!fileId || !sheet) return;
    setDetecting(true);
    try {
      const res = await post("/api/regions/detect", { fileId, sheet });
      const overlaps = (a: RangeJson, b: RangeJson) =>
        a.startRow <= b.endRow && b.startRow <= a.endRow && a.startCol <= b.endCol && b.startCol <= a.endCol;
      const next = [...regions];
      let added = 0;
      for (const sug of res.regions) {
        if (next.some((r) => r.designRange && overlaps(r.designRange, sug.range))) continue; // 已圈选过的位置不重复添加
        const rid = newId("rg", next.map((r) => r.regionId));
        const port = newId("out", next.map((r) => r.outputPortId));
        next.push(buildRegion(sug, sug.range, sug.name, rid, port));
        added++;
      }
      if (!added) {
        message.info(res.regions.length ? "识别到的区域都已存在" : "没有识别到表格区域，请手工框选");
        return;
      }
      setRegions(next);
      setActive(next[regions.length].regionId);
      if (next[regions.length].designRange) onFocus(next[regions.length].designRange);
      message.success(`识别出 ${added} 个区域，请逐个检查形态、范围与字段；不需要的可以删除`);
    } catch (e: any) {
      message.error("自动识别失败：" + e.message);
    } finally {
      setDetecting(false);
    }
  };

  const regionIssues = (rid: string) => issues.filter((i) => i.node === node.id && (!i.region || i.region === rid));

  return (
    <div>
      <Card size="small" title="Sheet 匹配规则" style={{ marginBottom: 8 }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Radio.Group value={sheetRule.match} onChange={(e) => onChange({ ...cfg, sheet: { ...sheetRule, match: e.target.value } })}
            options={[{ value: "EXACT", label: "按名称" }, { value: "REGEX", label: "多 Sheet 同构（正则）" }]} />
          {sheetRule.match === "REGEX" ? (
            <Space>
              <Input placeholder="例如 ^S\d+服$" value={sheetRule.value} onChange={(e) => onChange({ ...cfg, sheet: { ...sheetRule, value: e.target.value } })} />
              <Input placeholder="来源 Sheet 字段名" value={sheetRule.asColumn} onChange={(e) => onChange({ ...cfg, sheet: { ...sheetRule, asColumn: e.target.value } })} />
            </Space>
          ) : (
            <Select id="source-sheet" style={{ width: "100%" }} value={sheetRule.value || undefined} placeholder="选择 Sheet"
              onChange={(v) => onChange({ ...cfg, sheet: { match: "EXACT", value: v } })} options={sheets.map((s) => ({ value: s, label: s }))} />
          )}
          {sheetRule.match === "REGEX" && (() => {
            try {
              const rx = new RegExp(`^(?:${sheetRule.value})$`);
              const hit = sheets.filter((s) => rx.test(s));
              return <span className="cf-muted">命中：{hit.join("、") || "无"}</span>;
            } catch {
              return <span className="cf-err">正则不合法</span>;
            }
          })()}
        </Space>
      </Card>

      <Card size="small" title="区域" extra={
        <Space size={4}>
          <Tooltip title="按空行、空列自动切分当前 Sheet 并推荐形态、定位与字段，识别后可逐个调整">
            <Button id="auto-detect" size="small" loading={detecting} disabled={!fileId || !currentSheet()} onClick={autoDetect}>自动识别</Button>
          </Tooltip>
          <Tooltip title={selection ? `用选区 ${selection.a1} 新建` : "先在左侧表格中框选"}>
            <Button id="create-region" size="small" type="primary" disabled={!selection} onClick={createFromSelection}>用选区新建区域</Button>
          </Tooltip>
        </Space>
      } style={{ marginBottom: 8 }}>
        <List size="small" dataSource={regions} locale={{ emptyText: "点「自动识别」，或在左侧表格框选后新建区域" }}
          renderItem={(r: any, i: number) => {
            const n = regionIssues(r.regionId).filter((x) => x.severity === "ERROR").length;
            return (
              <List.Item style={{ cursor: "pointer", background: r.regionId === active ? "#f0f5ff" : undefined, paddingLeft: 6 }}
                onClick={() => { setActive(r.regionId); if (r.designRange) onFocus(r.designRange); }}
                actions={[
                  <Popconfirm key="d" title="删除该区域？" onConfirm={() => setRegions(regions.filter((x) => x.regionId !== r.regionId))}><a>删除</a></Popconfirm>,
                ]}>
                <Space>
                  <span style={{ display: "inline-block", width: 10, height: 10, background: REGION_COLORS[i % REGION_COLORS.length], border: "1px solid #91caff" }} />
                  <span>{r.name}</span>
                  <Tag>{SHAPES.find((s) => s.value === r.shape)?.label}</Tag>
                  <span className="cf-mono cf-muted">{r.designRange?.a1 || (r.designRange && toA1(r.designRange))}</span>
                  {n > 0 && <Tag color="red">✖ {n}</Tag>}
                </Space>
              </List.Item>
            );
          }} />
      </Card>

      {region && (
        <Card size="small" title={<Space>区域配置<Input size="small" value={region.name} onChange={(e) => upd({ name: e.target.value })} style={{ width: 140 }} /></Space>}
          extra={<Button size="small" onClick={() => onPreview(region)}>预览本区域</Button>}>
          <Space style={{ marginBottom: 8 }}>
            <span>设计范围</span>
            <Input id="region-range" size="small" className="cf-mono" style={{ width: 120 }} defaultValue={region.designRange ? toA1(region.designRange) : ""} key={region.regionId + (region.designRange ? toA1(region.designRange) : "")}
              onBlur={(e) => { const r = parseA1(e.target.value); if (r) upd({ designRange: r }); else message.error("范围格式应为 A1:B5"); }} />
            {selection && <Button size="small" onClick={() => upd({ designRange: { ...selection, a1: toA1(selection) } })}>用当前选区</Button>}
          </Space>
          <Collapse size="small" defaultActiveKey={["1", "2", "3"]} items={[
            { key: "1", label: "1 形态", children: <ShapeForm region={region} upd={upd} updOpt={updOpt} /> },
            { key: "2", label: "2 定位方式", children: <LocatorForm region={region} upd={upd} /> },
            { key: "3", label: "3 字段", children: region.shape === "IGNORE" ? <span className="cf-muted">屏蔽区不输出数据</span> : <ColumnsForm region={region} upd={upd} /> },
          ]} />
          {regionIssues(region.regionId).length > 0 && (
            <List size="small" style={{ marginTop: 8 }} header="本区域问题" dataSource={regionIssues(region.regionId).slice(0, 20)}
              renderItem={(i: any) => <List.Item><Tag color={i.severity === "ERROR" ? "red" : "orange"}>{i.code}</Tag>{i.message}</List.Item>} />
          )}
        </Card>
      )}
    </div>
  );
}

function ShapeForm({ region, upd, updOpt }: { region: any; upd: (p: any) => void; updOpt: (p: any) => void }) {
  const o = region.shapeOptions || {};
  const rec = region.recommended?.shape;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Radio.Group id="shape-radio" value={region.shape} onChange={(e) => upd({ shape: e.target.value })} size="small"
        options={SHAPES.map((s) => ({ value: s.value, label: s.value === rec ? `${s.label}（推荐）` : s.label }))} />
      {region.shape === "DETAIL" && (
        <Space wrap>
          表头行数 <InputNumber size="small" min={0} max={5} value={o.headerRows ?? 1} onChange={(v) => updOpt({ headerRows: v })} />
          方向 <Select size="small" value={o.orientation || "ROW"} onChange={(v) => updOpt({ orientation: v })} options={[{ value: "ROW", label: "按行" }, { value: "COLUMN", label: "按列（横向明细）" }]} />
          注释前缀 <Input size="small" style={{ width: 60 }} value={o.skipRows?.commentPrefix || ""} onChange={(e) => updOpt({ skipRows: { ...(o.skipRows || {}), blank: true, commentPrefix: e.target.value || undefined } })} />
        </Space>
      )}
      {region.shape === "KEY_VALUE" && (
        <Space wrap>
          Key 列 <InputNumber size="small" min={1} value={o.keyCol ?? 1} onChange={(v) => updOpt({ keyCol: v })} />
          Value 列 <InputNumber size="small" min={1} value={o.valueCol ?? 2} onChange={(v) => updOpt({ valueCol: v })} />
          重复 Key <Select size="small" value={o.duplicateKey || "ERROR"} onChange={(v) => updOpt({ duplicateKey: v })} options={[{ value: "ERROR", label: "报错" }, { value: "LAST", label: "取最后" }, { value: "ARRAY", label: "合并为列表" }]} />
          输出 <Select size="small" value={o.outputMode || "WIDE"} onChange={(v) => updOpt({ outputMode: v })} options={[{ value: "WIDE", label: "宽：1 行" }, { value: "LONG", label: "纵向：key-value 多行" }]} />
        </Space>
      )}
      {region.shape === "MATRIX" && (
        <Space direction="vertical">
          <Space wrap>
            行头列数 <InputNumber size="small" min={1} value={o.rowHeaderCols ?? 1} onChange={(v) => updOpt({ rowHeaderCols: v })} />
            列头行数 <InputNumber size="small" min={1} value={o.colHeaderRows ?? 1} onChange={(v) => updOpt({ colHeaderRows: v })} />
            值字段 <Input size="small" style={{ width: 90 }} value={o.valueName || ""} onChange={(e) => updOpt({ valueName: e.target.value })} />
          </Space>
          <Space wrap>
            行维度 <Input size="small" style={{ width: 120 }} value={(o.rowDims || []).join(",")} onChange={(e) => updOpt({ rowDims: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
            列维度 <Input size="small" style={{ width: 120 }} value={(o.colDims || []).join(",")} onChange={(e) => updOpt({ colDims: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
            指标层 <Input size="small" style={{ width: 90 }} value={o.metricLevel || ""} onChange={(e) => updOpt({ metricLevel: e.target.value || undefined })} />
          </Space>
          <Space wrap>
            排除关键词 <Input size="small" style={{ width: 140 }} value={(o.totalMarkers || []).join(",")} onChange={(e) => updOpt({ totalMarkers: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
            <Checkbox checked={o.dropEmpty ?? true} onChange={(e) => updOpt({ dropEmpty: e.target.checked })}>丢弃空交叉点</Checkbox>
          </Space>
        </Space>
      )}
      {region.shape === "SUMMARY" && (
        <Space wrap>
          布局 <Select size="small" value={o.layout || "ROW"} onChange={(v) => updOpt({ layout: v })} options={[{ value: "ROW", label: "按行" }, { value: "KV", label: "按键值" }]} />
          对齐区域 ID <Input size="small" style={{ width: 100 }} value={o.alignWith || ""} onChange={(e) => updOpt({ alignWith: e.target.value || undefined })} />
        </Space>
      )}
      {region.shape === "GROUPED_DETAIL" && (
        <Space wrap>
          分组字段 <Input size="small" style={{ width: 90 }} value={o.groupField || ""} onChange={(e) => updOpt({ groupField: e.target.value })} />
          识别 <Select size="small" value={o.groupRowDetector?.mode || "FIRST_CELL_ONLY"} onChange={(v) => updOpt({ groupRowDetector: { ...(o.groupRowDetector || {}), mode: v } })}
            options={[{ value: "FIRST_CELL_ONLY", label: "仅首格有值" }, { value: "REGEX", label: "正则" }, { value: "BOLD", label: "加粗" }]} />
          正则 <Input size="small" style={{ width: 110 }} value={o.groupRowDetector?.pattern || ""} onChange={(e) => updOpt({ groupRowDetector: { ...(o.groupRowDetector || {}), pattern: e.target.value || undefined } })} />
          小计 <Input size="small" style={{ width: 80 }} value={o.subtotalDetector?.pattern || ""} onChange={(e) => updOpt({ subtotalDetector: e.target.value ? { pattern: e.target.value } : undefined })} />
        </Space>
      )}
      {(region.shape === "FORM" || region.shape === "REPEATING_BLOCK") && (
        <Space direction="vertical" style={{ width: "100%" }}>
          {region.shape === "REPEATING_BLOCK" && (
            <Space wrap>
              块标题正则 <Input size="small" style={{ width: 120 }} value={o.blockAnchor || ""} onChange={(e) => updOpt({ blockAnchor: e.target.value })} />
              块大小 <InputNumber size="small" min={1} value={o.blockSize?.rows} onChange={(v) => updOpt({ blockSize: { ...(o.blockSize || {}), rows: v } })} /> 行 ×
              <InputNumber size="small" min={1} value={o.blockSize?.cols} onChange={(v) => updOpt({ blockSize: { ...(o.blockSize || {}), cols: v } })} /> 列
            </Space>
          )}
          <span className="cf-muted">字段位置（相对{region.shape === "FORM" ? "区域" : "块"}左上角，从 0 开始）：</span>
          <FieldOffsets value={(region.shape === "FORM" ? o.fields : o.innerOptions?.fields) || {}}
            onChange={(f) => (region.shape === "FORM" ? updOpt({ fields: f }) : updOpt({ innerShape: "FORM", innerOptions: { fields: f } }))} />
        </Space>
      )}
    </Space>
  );
}

function FieldOffsets({ value, onChange }: { value: Record<string, { row: number; col: number }>; onChange: (v: any) => void }) {
  const entries = Object.entries(value);
  return (
    <div>
      {entries.map(([k, v], i) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Input size="small" style={{ width: 90 }} value={k} onChange={(e) => { const n: any = {}; entries.forEach(([kk, vv], j) => (n[j === i ? e.target.value : kk] = vv)); onChange(n); }} />
          行 <InputNumber size="small" min={0} value={v.row} onChange={(x) => onChange({ ...value, [k]: { ...v, row: x ?? 0 } })} />
          列 <InputNumber size="small" min={0} value={v.col} onChange={(x) => onChange({ ...value, [k]: { ...v, col: x ?? 0 } })} />
          <a onClick={() => { const n = { ...value }; delete n[k]; onChange(n); }}>删除</a>
        </Space>
      ))}
      <Button size="small" onClick={() => onChange({ ...value, [`字段${entries.length + 1}`]: { row: 0, col: 0 } })}>+ 字段</Button>
    </div>
  );
}

function LocatorForm({ region, upd }: { region: any; upd: (p: any) => void }) {
  const loc = region.locator || { type: "FIXED" };
  const set = (p: any) => upd({ locator: { ...loc, ...p } });
  const rec = region.recommended?.locator;
  const rows = loc.end?.rows || { mode: "UNTIL_BLANK_ROWS", n: 1 };
  const cols = loc.end?.cols || { mode: "UNTIL_BLANK_HEADER" };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Radio.Group size="small" value={loc.type} onChange={(e) => {
        const t = e.target.value;
        if (t === "ANCHOR") set({ type: t, start: loc.start || { text: "", match: "EXACT", offset: { row: 0, col: 0 } }, end: { rows, cols } });
        else upd({ locator: t === "AUTO_EXPAND" ? { type: t, blankRowsToStop: 1 } : { type: t } });
      }} options={[{ value: "FIXED", label: "固定坐标" }, { value: "ANCHOR", label: "锚点" }, { value: "AUTO_EXPAND", label: "自动扩展" }].map((o) => ({ ...o, label: o.value === rec ? `${o.label}（推荐）` : o.label }))} />
      {loc.type === "ANCHOR" && (
        <>
          <Space wrap>
            起点文本 <Input size="small" style={{ width: 130 }} value={loc.start?.text} onChange={(e) => set({ start: { ...loc.start, text: e.target.value } })} />
            <Select size="small" value={loc.start?.match || "EXACT"} onChange={(v) => set({ start: { ...loc.start, match: v } })} options={[{ value: "EXACT", label: "等于" }, { value: "CONTAINS", label: "包含" }, { value: "PREFIX", label: "开头是" }, { value: "REGEX", label: "正则" }]} />
            行偏移 <InputNumber size="small" style={{ width: 60 }} value={loc.start?.offset?.row ?? 0} onChange={(v) => set({ start: { ...loc.start, offset: { ...(loc.start?.offset || {}), row: v ?? 0 } } })} />
          </Space>
          <Space wrap>
            终点（行）
            <Select size="small" style={{ width: 130 }} value={rows.mode} onChange={(v) => set({ end: { rows: { ...rows, mode: v }, cols } })}
              options={[{ value: "UNTIL_BLANK_ROWS", label: "到连续空行" }, { value: "UNTIL_ANCHOR", label: "到某文本之前" }, { value: "FIXED_SIZE", label: "固定行数" }, { value: "SHEET_END", label: "到表尾" }]} />
            {rows.mode === "UNTIL_ANCHOR" && <Input size="small" style={{ width: 100 }} value={rows.text} onChange={(e) => set({ end: { rows: { ...rows, text: e.target.value }, cols } })} />}
            {(rows.mode === "FIXED_SIZE" || rows.mode === "UNTIL_BLANK_ROWS") && <InputNumber size="small" style={{ width: 60 }} min={1} value={rows.n ?? 1} onChange={(v) => set({ end: { rows: { ...rows, n: v }, cols } })} />}
          </Space>
          <Space wrap>
            终点（列）
            <Select size="small" style={{ width: 130 }} value={cols.mode} onChange={(v) => set({ end: { rows, cols: { ...cols, mode: v } } })}
              options={[{ value: "UNTIL_BLANK_HEADER", label: "到空表头" }, { value: "FIXED_SIZE", label: "固定列数" }, { value: "SHEET_END", label: "到最后一列" }]} />
            {cols.mode === "FIXED_SIZE" && <InputNumber size="small" style={{ width: 60 }} min={1} value={cols.n} onChange={(v) => set({ end: { rows, cols: { ...cols, n: v } } })} />}
          </Space>
        </>
      )}
      {loc.type === "AUTO_EXPAND" && (
        <Space>连续空行数 <InputNumber size="small" min={1} value={loc.blankRowsToStop ?? 1} onChange={(v) => set({ blankRowsToStop: v })} /></Space>
      )}
    </Space>
  );
}

function ColumnsForm({ region, upd }: { region: any; upd: (p: any) => void }) {
  const cols: any[] = region.columns || [];
  const set = (i: number, p: any) => upd({ columns: cols.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  return (
    <div>
      <Table size="small" rowKey={(_, i) => String(i)} pagination={false} dataSource={cols} scroll={{ x: 640 }} className="field-table"
        columns={[
          { title: "表头原文", dataIndex: "source", width: 170, render: (v, _, i) => <Input size="small" value={v} onChange={(e) => set(i, { source: e.target.value })} /> },
          { title: "字段名", dataIndex: "field", width: 160, render: (v, _, i) => <Input size="small" className="cf-mono field-name" value={v} status={/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(v || "") ? undefined : "error"} onChange={(e) => set(i, { field: e.target.value })} /> },
          { title: "类型", dataIndex: "type", width: 120, render: (v, _, i) => <Select size="small" style={{ width: 115 }} value={v || "string"} onChange={(x) => set(i, { type: x })} options={TYPES.map((t) => ({ value: t, label: t }))} showSearch /> },
          { title: "必填", dataIndex: "required", width: 44, render: (v, _, i) => <Checkbox checked={!!v} onChange={(e) => set(i, { required: e.target.checked })} /> },
          { title: "主键", dataIndex: "isKey", width: 44, render: (v, _, i) => <Checkbox checked={!!v} onChange={(e) => set(i, { isKey: e.target.checked })} /> },
          { title: "", width: 30, render: (_, __, i) => <a onClick={() => upd({ columns: cols.filter((_c, j) => j !== i) })}>✕</a> },
        ]}
        expandable={{
          expandedRowRender: (c: any, i: number) => (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Space wrap>空值写法 <Input size="small" style={{ width: 160 }} placeholder="默认：空、-、N/A、NULL" value={(c.nullTokens || []).join(",")} onChange={(e) => set(i, { nullTokens: e.target.value ? e.target.value.split(",") : undefined })} />
                默认值 <Input size="small" style={{ width: 80 }} value={c.default ?? ""} onChange={(e) => set(i, { default: e.target.value === "" ? undefined : e.target.value })} /></Space>
              {c.type === "enum" && <Space>枚举映射 <Input size="small" style={{ width: 260 }} placeholder='{"普通":1,"稀有":2}' defaultValue={c.enumMap ? JSON.stringify(c.enumMap) : ""} onBlur={(e) => { try { set(i, { enumMap: JSON.parse(e.target.value) }); } catch { /* 忽略 */ } }} /></Space>}
              {c.type === "bool" && <Space>布尔写法 <Input size="small" style={{ width: 120 }} placeholder="真：是,开" value={c.boolTokens?.true?.join(",") || ""} onChange={(e) => set(i, { boolTokens: { ...(c.boolTokens || {}), true: e.target.value.split(",") } })} /><Input size="small" style={{ width: 120 }} placeholder="假：否,关" value={c.boolTokens?.false?.join(",") || ""} onChange={(e) => set(i, { boolTokens: { ...(c.boolTokens || {}), false: e.target.value.split(",") } })} /></Space>}
              {String(c.type || "").startsWith("list") && <Space>单元格内分隔符 <Input size="small" style={{ width: 40 }} value={c.split?.item ?? "|"} onChange={(e) => set(i, { split: { ...(c.split || {}), item: e.target.value } })} /> 键值分隔 <Input size="small" style={{ width: 40 }} value={c.split?.kv ?? ":"} onChange={(e) => set(i, { split: { ...(c.split || {}), kv: e.target.value } })} /></Space>}
            </Space>
          ),
        }} />
      <Button size="small" style={{ marginTop: 6 }} onClick={() => upd({ columns: [...cols, { source: "", field: `col${cols.length + 1}`, type: "string" }] })}>+ 字段</Button>
    </div>
  );
}
