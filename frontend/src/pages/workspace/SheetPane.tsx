import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import sheetsCoreZhCN from "@univerjs/preset-sheets-core/locales/zh-CN";
import "@univerjs/preset-sheets-core/lib/index.css";
import { createUniver, LocaleType, mergeLocales } from "@univerjs/presets";
import { Button, Spin, Tabs, Tag } from "antd";
import { useEffect, useRef, useState } from "react";
import { get } from "../../api";
import { RangeJson, toA1 } from "../../dsl";

export interface Overlay {
  id: string;
  name: string;
  range: RangeJson;
  color: string;
  ignore?: boolean;
}

interface Props {
  fileId: number | null;
  sheets: string[];
  sheet: string | null;
  onSheet: (s: string) => void;
  overlays: Overlay[];
  highlight?: { cell: string; nonce: number } | null;
  focusRange?: { range: RangeJson; nonce: number } | null;
  onSelection: (r: RangeJson | null) => void;
  toolbar?: React.ReactNode;
}

const PAGE_ROWS = 1000;

function a1ToRC(cell: string): [number, number] | null {
  const m = cell.toUpperCase().match(/^([A-Z]+)(\d+)$/);
  if (!m) return null;
  let c = 0;
  for (const ch of m[1]) c = c * 26 + (ch.charCodeAt(0) - 64);
  return [Number(m[2]), c];
}

/** 只读表格区（WIREFRAME P3-1 G2）：服务端网格转换的 Univer 数据（D5）+ 区域彩色覆盖 + 单元格高亮。 */
export default function SheetPane({ fileId, sheets, sheet, onSheet, overlays, highlight, focusRange, onSelection, toolbar }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<any>(null);
  const [snap, setSnap] = useState<any>(null);
  const [rowsLoaded, setRowsLoaded] = useState(PAGE_ROWS);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<RangeJson | null>(null);
  const unitId = useRef<string | null>(null);

  // 创建 Univer 实例（组件生命周期内一次）
  useEffect(() => {
    if (!host.current) return;
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: mergeLocales(sheetsCoreZhCN) },
      presets: [UniverSheetsCorePreset({ container: host.current, header: false, toolbar: false, footer: false, formulaBar: false, contextMenu: false } as any)],
    });
    api.current = univerAPI;
    const d = univerAPI.addEvent(univerAPI.Event.SelectionChanged, (p: any) => {
      const s = p.selections?.[0];
      if (!s || (unitId.current && p.workbook?.getId?.() !== unitId.current)) return;
      const r = { startRow: s.startRow + 1, startCol: s.startColumn + 1, endRow: s.endRow + 1, endCol: s.endColumn + 1 };
      const rj = { ...r, a1: toA1(r) };
      setSel(rj);
      onSelection(rj);
    });
    return () => {
      d.dispose();
      univer.dispose();
      api.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 读取 Sheet 数据
  useEffect(() => {
    if (!fileId || !sheet) {
      setSnap(null);
      return;
    }
    setLoading(true);
    get(`/api/files/${fileId}/sheets/${encodeURIComponent(sheet)}/univer?rows=1-${rowsLoaded}`)
      .then(setSnap)
      .catch(() => setSnap(null))
      .finally(() => setLoading(false));
  }, [fileId, sheet, rowsLoaded]);

  // 生成工作簿：值 + 覆盖层样式
  useEffect(() => {
    const univerAPI = api.current;
    if (!univerAPI || !snap) return;
    if (unitId.current) {
      try { univerAPI.disposeUnit(unitId.current); } catch { /* 已释放 */ }
    }
    const cellData: Record<number, Record<number, any>> = {};
    const cell = (r: number, c: number) => ((cellData[r] ||= {})[c] ||= {});
    for (const [r, cols] of Object.entries<any>(snap.cellData || {})) {
      for (const [c, v] of Object.entries<any>(cols)) {
        const x = cell(Number(r), Number(c));
        x.v = v.v;
        x.t = v.t;
        if (v.s === "bold") x.s = { bl: 1 };
      }
    }
    const style = (r: number, c: number, patch: any) => {
      const x = cell(r, c);
      x.s = { ...(typeof x.s === "object" ? x.s : {}), ...patch };
    };
    const maxR = Math.min(snap.rowCount, snap.loadedRows?.to ?? snap.rowCount);
    for (const o of overlays) {
      const { startRow, endRow, startCol, endCol } = o.range;
      for (let r = startRow; r <= Math.min(endRow, maxR); r++) {
        for (let c = startCol; c <= endCol; c++) {
          style(r - 1, c - 1, o.ignore ? { bg: { rgb: "#f0f0f0" }, cl: { rgb: "#999999" } } : { bg: { rgb: o.color } });
        }
      }
      style(startRow - 1, startCol - 1, { bd: { t: { s: 2, cl: { rgb: "#1677ff" } }, l: { s: 2, cl: { rgb: "#1677ff" } } } });
    }
    for (const m of snap.cfMarks || []) style(m.row, m.col, { bg: { rgb: m.kind === "ERROR" ? "#ffccc7" : "#ffe7ba" } });
    if (highlight?.cell) {
      const rc = a1ToRC(highlight.cell);
      if (rc) style(rc[0] - 1, rc[1] - 1, { bg: { rgb: "#ff7875" }, bd: { b: { s: 2, cl: { rgb: "#cf1322" } }, t: { s: 2, cl: { rgb: "#cf1322" } } } });
    }
    const id = `wb-${fileId}-${snap.name}-${Date.now()}`;
    const data = {
      id,
      name: snap.name,
      appVersion: "cellflow",
      locale: LocaleType.ZH_CN,
      styles: {},
      sheetOrder: ["s1"],
      sheets: {
        s1: {
          id: "s1",
          name: snap.name,
          rowCount: Math.max(snap.rowCount + 20, 100),
          columnCount: Math.max(snap.columnCount + 5, 26),
          cellData,
          mergeData: snap.mergeData,
          rowData: snap.rowData,
          columnData: snap.columnData,
        },
      },
    };
    const wb = univerAPI.createWorkbook(data);
    unitId.current = wb.getId();
    try { wb.setEditable(false); } catch { /* 旧版本无此方法 */ }
    if (highlight?.cell) {
      const rc = a1ToRC(highlight.cell);
      if (rc) {
        try {
          const ws = wb.getActiveSheet();
          ws.getRange(rc[0] - 1, rc[1] - 1).activate();
          ws.scrollToCell?.(Math.max(rc[0] - 4, 0), Math.max(rc[1] - 2, 0));
        } catch { /* 忽略 */ }
      }
    }
  }, [snap, overlays, highlight, fileId]);

  // 聚焦某个区域（点击区域列表）
  useEffect(() => {
    const univerAPI = api.current;
    if (!univerAPI || !focusRange || !unitId.current) return;
    try {
      const ws = univerAPI.getActiveWorkbook().getActiveSheet();
      const r = focusRange.range;
      ws.getRange(r.startRow - 1, r.startCol - 1, r.endRow - r.startRow + 1, r.endCol - r.startCol + 1).activate();
      ws.scrollToCell?.(Math.max(r.startRow - 3, 0), Math.max(r.startCol - 2, 0));
    } catch { /* 忽略 */ }
  }, [focusRange]);

  return (
    <div className="cf-sheet">
      <div style={{ padding: "6px 8px", borderBottom: "1px solid #eee", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {toolbar}
        <span style={{ marginLeft: "auto" }} />
        {sel && <Tag className="cf-mono" id="current-selection">选区 {sel.a1}</Tag>}
        {snap && snap.rowCount > rowsLoaded && (
          <Button size="small" onClick={() => setRowsLoaded(rowsLoaded + PAGE_ROWS)}>加载更多行（共 {snap.rowCount} 行）</Button>
        )}
      </div>
      <div className="cf-sheet-host">
        {loading && <Spin style={{ position: "absolute", zIndex: 2, left: "50%", top: "40%" }} />}
        {!fileId && <div className="cf-muted" style={{ padding: 24 }}>请先在操作栏上传方案的样例文件</div>}
        <div ref={host} style={{ position: "absolute", inset: 0 }} />
      </div>
      <Tabs size="small" style={{ margin: 0, padding: "0 8px" }} activeKey={sheet || undefined} onChange={onSheet}
        items={sheets.map((s) => ({ key: s, label: <span>{s}{overlays.length > 0 && s === sheet ? <Tag style={{ marginLeft: 4 }}>{overlays.filter((o) => !o.ignore).length}</Tag> : null}</span> }))} />
    </div>
  );
}
