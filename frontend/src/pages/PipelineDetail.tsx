import { Alert, App, Button, Checkbox, Descriptions, Empty, Form, Input, Modal, Result, Space, Spin, Table, Tabs, Tag } from "antd";
import { lazy, Suspense, useEffect, useState } from "react";
import { ApiError, get, opCall, patch, post } from "../api";
import Changes from "../components/Changes";
import { fmtTime, ReleaseKind } from "../components/StatusTag";
import { navigate } from "../router";
import Jobs from "./Jobs";

const Workspace = lazy(() => import("./workspace/Workspace"));

const TABS = [
  ["canvas", "画布编辑"],
  ["versions", "版本"],
  ["jobs", "任务"],
  ["releases", "发布历史"],
  ["settings", "方案设置"],
];

/** 方案详情（标签页）：P3 / P4 / P5 / P7 / P8，以及全屏的 P4-1 发布页。 */
export default function PipelineDetail({ id, tab, query }: { id: number; tab: string; query: URLSearchParams }) {
  const [p, setP] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => get(`/api/pipelines/${id}`).then(setP).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [id]);
  if (err) return <Result status="404" title="方案不存在" subTitle={err} extra={<Button onClick={() => navigate("/pipelines")}>返回方案列表</Button>} />;
  if (!p) return <Spin style={{ margin: 48 }} />;
  if (tab === "publish") return <PublishPage p={p} onDone={load} />;
  let body: JSX.Element;
  if (tab === "versions") body = <Versions p={p} reload={load} />;
  else if (tab === "jobs") body = <Jobs query={query} pipelineId={p.id} />;
  else if (tab === "releases") body = <Releases p={p} reload={load} />;
  else if (tab === "settings") body = <PipelineSettings p={p} reload={load} />;
  else body = <Suspense fallback={<Spin style={{ margin: 48 }} />}><Workspace pipeline={p} reload={load} /></Suspense>;
  return (
    <div>
      <div className="cf-crumb">
        <a onClick={() => navigate("/pipelines")}>方案</a> / <span className="cf-mono">{p.code}</span> {p.name}
        <span style={{ marginLeft: 12 }}>{p.publishedRev ? <Tag color="green">生效 v{p.publishedRev}</Tag> : <Tag>未发布</Tag>}</span>
        {p.frozen && <Tag color="purple">已冻结</Tag>}
      </div>
      {p.frozen && tab !== "canvas" && (
        <div className="cf-banner-frozen" id="frozen-banner">方案已冻结：调用方提交的写表任务会被拒绝（只校验与试跑不受影响）。可在「发布历史」或「方案设置」中解冻。</div>
      )}
      <Tabs activeKey={tab} style={{ background: "#fff", padding: "0 16px", marginBottom: 0 }} onChange={(k) => navigate(`/pipelines/${id}/${k}`)}
        items={TABS.map(([k, t]) => ({ key: k, label: t }))} />
      {body}
    </div>
  );
}

// ---------------- P4 版本列表 ----------------
function DiffList({ d }: { d: any }) {
  if (!d) return null;
  const rows: [string, string, any[]][] = [
    ["green", "新增节点", d.nodesAdded], ["blue", "修改节点", d.nodesChanged], ["red", "删除节点", d.nodesRemoved],
  ];
  const empty = rows.every(([, , l]) => !l?.length) && !d.edgesAdded?.length && !d.edgesRemoved?.length && !d.bindingsChanged?.length;
  if (empty) return <span className="cf-muted">无配置差异</span>;
  return (
    <div id="dsl-diff">
      {rows.map(([c, t, l]) => l?.length ? <div key={t}>{t}：{l.map((n: any) => <Tag key={n.id} color={c}>{n.label || n.id}（{n.type}）</Tag>)}</div> : null)}
      {(d.edgesAdded?.length || d.edgesRemoved?.length) ? <div>连线：新增 {d.edgesAdded.length} 条，删除 {d.edgesRemoved.length} 条</div> : null}
      {d.bindingsChanged?.length ? <div>目标表绑定变化：{d.bindingsChanged.map((b: string) => <Tag key={b} color="orange">{b}</Tag>)}</div> : null}
    </div>
  );
}

function Versions({ p, reload }: { p: any; reload: () => void }) {
  const { message } = App.useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [diff, setDiff] = useState<{ rev: number; d: any } | null>(null);
  const [view, setView] = useState<any>(null);
  const load = () => get(`/api/pipelines/${p.id}/revisions`).then(setRows);
  useEffect(() => { load(); }, [p.id]);
  const republish = async (rev: number) => {
    const r = await opCall({ title: `重新发布 v${rev}`, summary: `方案 ${p.code} 的生效版本将从 v${p.publishedRev ?? "—"} 切换为 v${rev}，之后提交的任务使用该版本。`, reasonRequired: true },
      "POST", `/api/pipelines/${p.id}/revisions/${rev}/republish`).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success(`v${rev} 已生效`); load(); reload(); }
  };
  return (
    <div className="cf-page">
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={() => navigate(`/pipelines/${p.id}/publish`)}>发布草稿…</Button>
      </Space>
      <Table rowKey="id" dataSource={rows} pagination={false} locale={{ emptyText: <Empty description="还没有发布过版本" /> }}
        columns={[
          { title: "版本", dataIndex: "rev", render: (v) => `v${v}` },
          { title: "状态", dataIndex: "status", render: (v) => v === "PUBLISHED" ? <Tag color="green">已发布·生效中</Tag> : v === "RETIRED" ? <Tag>已退役</Tag> : <Tag>{v}</Tag> },
          { title: "发布人", dataIndex: "publishedBy" },
          { title: "发布时间", dataIndex: "publishedAt", render: fmtTime },
          { title: "发布说明", dataIndex: "note" },
          { title: "回归", dataIndex: "regression", render: (s) => s ? <span>{s.files} 个文件{s.filesWithNewErrors ? <span className="cf-err">，{s.filesWithNewErrors} 个报错</span> : ""}，{s.filesChanged} 个有差异</span> : "—" },
          {
            title: "操作", render: (_: any, r: any) => (
              <Space>
                <a onClick={() => get(`/api/pipelines/${p.id}/revisions/${r.rev}`).then(setView)}>查看</a>
                {r.status !== "PUBLISHED" && <a onClick={() => get(`/api/pipelines/${p.id}/revisions/${r.rev}/diff?against=live`).then((d) => setDiff({ rev: r.rev, d }))}>与生效版本对比</a>}
                {r.status !== "PUBLISHED" && <a className="republish" onClick={() => republish(r.rev)}>重新发布此版本 🔒</a>}
              </Space>
            ),
          },
        ]} />
      <Modal open={!!diff} title={`v${diff?.rev} 相对生效版本的差异`} footer={null} onCancel={() => setDiff(null)}><DiffList d={diff?.d} /></Modal>
      <Modal open={!!view} title={`v${view?.rev}（只读）`} footer={null} width={720} onCancel={() => setView(null)}>
        {view && (
          <Table size="small" rowKey="id" pagination={false} dataSource={view.dsl.nodes}
            columns={[{ title: "节点", dataIndex: "id" }, { title: "类型", dataIndex: "type" }, { title: "名称", dataIndex: "label" },
              { title: "连出", render: (_: any, n: any) => view.dsl.edges.filter((e: any) => e.source.nodeId === n.id).map((e: any) => `${e.source.portId}→${e.target.nodeId}.${e.target.portId}`).join("，") }]} />
        )}
      </Modal>
    </div>
  );
}

// ---------------- P4-1 发布方案版本 ----------------
function PublishPage({ p, onDone }: { p: any; onDone: () => void }) {
  const { message } = App.useApp();
  const [draft, setDraft] = useState<any>(null);
  const [diff, setDiff] = useState<any>(null);
  const [reg, setReg] = useState<any>(null);
  const [regErr, setRegErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<any[]>([]);
  const next = (p.publishedRev || 0) + 1;
  useEffect(() => {
    get(`/api/pipelines/${p.id}/draft`).then(setDraft);
    get(`/api/pipelines/${p.id}/draft/diff`).then(setDiff);
    post(`/api/pipelines/${p.id}/regression`).then(setReg).catch((e) => setRegErr(e.message));
  }, [p.id]);
  const bad = reg?.summary?.filesWithNewErrors || 0;
  const publish = async () => {
    setBusy(true);
    setErrors([]);
    try {
      const summary = `将草稿发布为 v${next}（当前生效 ${p.publishedRev ? "v" + p.publishedRev : "无"}），之后提交的任务使用新版本。`;
      const r = await opCall({ title: `发布 ${p.code} v${next}`, summary, danger: bad ? `新版本在 ${bad} 个历史文件上报错` : undefined },
        "POST", `/api/pipelines/${p.id}/publish`, (a) => ({ draftVersion: draft.draftVersion, note: note || a.reason || null, regressionId: reg?.id }));
      if (r) {
        message.success(`已发布 v${r.rev}`);
        onDone();
        navigate(`/pipelines/${p.id}/versions`);
      }
    } catch (e) {
      if (e instanceof ApiError && e.data?.errors) setErrors(e.data.errors);
      else message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="cf-page" style={{ maxWidth: 1100, margin: "0 auto" }}>
      <h2 id="publish-title">将草稿发布为 v{next}（当前生效 {p.publishedRev ? `v${p.publishedRev}` : "无"}）</h2>
      <h3>配置差异</h3>
      {diff ? <DiffList d={diff} /> : <Spin />}
      <h3 style={{ marginTop: 16 }}>历史文件回归</h3>
      {bad > 0 && <Alert type="error" showIcon style={{ marginBottom: 8 }} id="regression-warning" message={`新版本在 ${bad} 个历史文件上出现 ERROR，请确认后再发布`} />}
      {regErr ? <Alert type="warning" showIcon message={`回归未能运行：${regErr}`} /> : !reg ? <Spin tip="正在用草稿重跑最近的历史文件…"><div style={{ height: 60 }} /></Spin> : reg.items.length === 0 ? (
        <Alert type="info" showIcon message="没有可回归的历史文件（首次发布或还没有成功的任务）" />
      ) : (
        <Table size="small" rowKey="jobId" pagination={false} dataSource={reg.items} id="regression-table"
          rowClassName={(r: any) => (r.new.errors > 0 ? "cf-row-error" : "")}
          expandable={{ expandedRowRender: (r: any) => (
            <div>
              {r.new.issues.map((i: any, k: number) => <div key={k} className="cf-err">{i.sheet ? `${i.sheet}!${i.cell} ` : ""}{i.message}</div>)}
              {r.tables.map((t: any) => (
                <div key={t.dataset}><b>{t.table}</b> {t.note || ""}
                  {(t.sample || []).map((c: any, k: number) => <div key={k} className="cf-mono">{c.op} {JSON.stringify(c.rowKey || c.after || c.before)}{c.changedFields ? " : " + c.changedFields.join(",") : ""}</div>)}
                </div>
              ))}
            </div>
          ) }}
          columns={[
            { title: "文件", dataIndex: "fileName" },
            { title: "任务时间", dataIndex: "submittedAt", render: fmtTime },
            { title: `v${p.publishedRev ?? "-"} 结果`, render: (_: any, r: any) => `ERROR ${r.old.errors} / WARN ${r.old.warns}` },
            { title: `v${next} 结果`, render: (_: any, r: any) => <span className={r.new.errors ? "cf-err" : ""}>ERROR {r.new.errors} / WARN {r.new.warns}</span> },
            { title: "输出差异", render: (_: any, r: any) => r.tables.map((t: any) => <div key={t.dataset}>{t.table}：{t.changes ? `+${t.changes.c} ~${t.changes.u} -${t.changes.d}` : t.note}</div>) },
          ]} />
      )}
      {errors.length > 0 && <Alert type="error" showIcon style={{ marginTop: 12 }} message="方案存在错误，不能发布" description={<ul>{errors.map((e, i) => <li key={i}>{e.node ? `[${e.node}] ` : ""}{e.message}</li>)}</ul>} />}
      <h3 style={{ marginTop: 16 }}>发布说明</h3>
      <Input.TextArea id="publish-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="这次改了什么" />
      <Space style={{ marginTop: 12, float: "right" }}>
        <Button onClick={() => navigate(`/pipelines/${p.id}/canvas`)}>取消</Button>
        <Button type="primary" id="do-publish" loading={busy} disabled={!draft} onClick={publish}>发布 🔒</Button>
      </Space>
    </div>
  );
}

// ---------------- P7 发布历史 + P7-1 回滚 ----------------
async function toggleFreeze(p: any, message: any, reload: () => void) {
  const freeze = !p.frozen;
  const r = await opCall({ title: freeze ? `冻结方案 ${p.code}` : `解冻方案 ${p.code}`, reasonRequired: true,
    summary: freeze ? "冻结后调用方提交的写表任务会被拒绝，业务表保持当前内容。" : "解冻后调用方可以重新提交写表任务。" },
    "POST", `/api/pipelines/${p.id}/${freeze ? "freeze" : "unfreeze"}`).catch((e) => { message.error(e.message); return null; });
  if (r) { message.success(freeze ? "已冻结" : "已解冻"); reload(); }
}

function Releases({ p, reload }: { p: any; reload: () => void }) {
  const { message } = App.useApp();
  const [data, setData] = useState<any>(null);
  const [target, setTarget] = useState<any>(null);
  const load = () => get(`/api/pipelines/${p.id}/releases`).then(setData);
  useEffect(() => { load(); }, [p.id, p.frozen]);
  const live = data?.rows.find((r: any) => r.live);
  return (
    <div className="cf-page">
      <Space style={{ marginBottom: 12 }} size="large">
        <span id="live-release">线上：{live ? <b>发布 #{live.id}</b> : "尚无发布"}{live ? `（${fmtTime(live.publishedAt || live.createdAt)}）` : ""}</span>
        <span>状态：{p.frozen ? <Tag color="purple">已冻结</Tag> : <Tag color="green">正常</Tag>}</span>
        <Button id="freeze-toggle" onClick={() => toggleFreeze(p, message, () => { reload(); load(); })}>{p.frozen ? "解冻 🔒" : "冻结 🔒"}</Button>
      </Space>
      <Table rowKey="id" dataSource={data?.rows || []} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="还没有发布记录" /> }}
        expandable={{ expandedRowRender: (r: any) => <Changes url={`/api/releases/${r.id}/changes`} tables={r.tables} />, rowExpandable: (r: any) => r.kind !== "BASELINE" }}
        columns={[
          { title: "发布", dataIndex: "id", render: (v, r: any) => <span>#{v} {r.live && <Tag color="green">生效中</Tag>}</span> },
          { title: "类型", dataIndex: "kind", render: (v, r: any) => <span><ReleaseKind s={v} />{r.rollbackTo ? <span className="cf-muted">→ #{r.rollbackTo}</span> : null}</span> },
          { title: "状态", dataIndex: "status", render: (v) => v === "PUBLISHED" ? "" : <Tag color="red">{v}</Tag> },
          { title: "来源任务", dataIndex: "jobId", render: (v) => v ? <a onClick={() => navigate(`/jobs/${v}`)}>#{v}</a> : "—" },
          { title: "操作人", dataIndex: "operator" },
          { title: "时间", render: (_: any, r: any) => fmtTime(r.publishedAt || r.createdAt) },
          { title: "表与变更", render: (_: any, r: any) => <>
            <div className="cf-muted">{r.tables.join("，")}</div>
            {Object.entries<any>(r.changeSummary || {}).map(([ds, ch]) => <div key={ds}>{ds}：+{ch.c} ~{ch.u} -{ch.d}</div>)}
          </> },
          { title: "理由", dataIndex: "reason", ellipsis: true },
          {
            title: "", render: (_: any, r: any) => (
              <Button size="small" className="rollback-btn" disabled={r.live || !r.available} title={!r.available && r.status === "PUBLISHED" ? "已超过保留期" : ""}
                onClick={() => setTarget(r)}>回滚到此版本</Button>
            ),
          },
        ]} />
      {target && live && <RollbackModal p={p} live={live} target={target} onClose={(done) => { setTarget(null); if (done) { load(); reload(); } }} />}
    </div>
  );
}

function RollbackModal({ p, live, target, onClose }: { p: any; live: any; target: any; onClose: (done: boolean) => void }) {
  const { message } = App.useApp();
  const [pre, setPre] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [freeze, setFreeze] = useState(true);
  const [confirmDrift, setConfirmDrift] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    post(`/api/pipelines/${p.id}/rollback/preview`, { targetReleaseId: target.id }).then(setPre).catch((e) => setErr(e.message));
  }, [target.id]);
  const run = async () => {
    setBusy(true);
    try {
      const r = await opCall({ title: `回滚 ${p.code}`, reasonRequired: true, danger: pre?.drift ? "检测到业务表被外部改动，将被覆盖" : undefined,
        summary: `线上发布 #${live.id} → 回滚到 #${target.id}（${pre?.mode === "FAST_SWAP" ? "秒级互换备份表" : "从快照重写"}）${freeze ? "，回滚后冻结方案" : ""}。` },
        "POST", `/api/pipelines/${p.id}/rollback`, (a) => ({ targetReleaseId: target.id, expectedLiveReleaseId: live.id, reason: a.reason, freeze, confirmDrift }));
      if (r) { message.success(`已回滚，新的生效发布 #${r.releaseId}`); onClose(true); }
    } catch (e) {
      if (e instanceof ApiError && e.code === "LIVE_STATE_CONFLICT") { message.warning("线上版本已变化，已刷新"); onClose(true); }
      else message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open width={760} title={`回滚 ${p.code}：线上 #${live.id} → 目标 #${target.id}`} onCancel={() => onClose(false)}
      okText="回滚… 🔒" okButtonProps={{ danger: true, id: "do-rollback", loading: busy, disabled: !pre || !pre.compatible || (pre.drift && !confirmDrift) }} onOk={run} cancelText="取消">
      {err && <Alert type="error" showIcon message={err} />}
      {!pre && !err && <Spin />}
      {pre && (
        <div id="rollback-preview">
          <p>执行方式：<Tag color={pre.mode === "FAST_SWAP" ? "green" : "blue"}>{pre.mode === "FAST_SWAP" ? "秒级互换备份表" : "从快照重写"}</Tag></p>
          <Table size="small" rowKey="dataset" pagination={false} dataSource={pre.tables} columns={[
            { title: "表", dataIndex: "table" },
            { title: "新增/修改/删除", render: (_: any, t: any) => `${t.changes.c} / ${t.changes.u} / ${t.changes.d}` },
            { title: "检查", render: (_: any, t: any) => <>
              {t.schemaProblems?.length ? t.schemaProblems.map((s: any, i: number) => <div key={i} className="cf-err">{typeof s === "string" ? s : s.message}</div>) : <Tag color="green">结构兼容</Tag>}
              {t.drift && <Tag color="orange">被外部改动</Tag>}
            </> },
          ]} />
          {!pre.compatible && <Alert style={{ marginTop: 8 }} type="error" showIcon message="目标表结构与回滚版本不兼容，不能回滚" />}
          {pre.drift && <Alert style={{ marginTop: 8 }} type="warning" showIcon message={<Checkbox checked={confirmDrift} onChange={(e) => setConfirmDrift(e.target.checked)}>检测到业务表被外部改动，我确认覆盖</Checkbox>} />}
          <div style={{ marginTop: 12 }}><Checkbox id="rollback-freeze" checked={freeze} onChange={(e) => setFreeze(e.target.checked)}>回滚后冻结方案</Checkbox></div>
        </div>
      )}
    </Modal>
  );
}

// ---------------- P8 方案设置 ----------------
function PipelineSettings({ p, reload }: { p: any; reload: () => void }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [clients, setClients] = useState<any[]>([]);
  const [dss, setDss] = useState<any[]>([]);
  useEffect(() => {
    form.setFieldsValue({ name: p.name, description: p.description });
    get<any[]>("/api/client-apps").then((l) => setClients(l.filter((c) => (c.allowedPipelines || []).includes(p.code))));
    get<any[]>("/api/datasources").then(setDss);
  }, [p.id]);
  return (
    <div className="cf-page" style={{ maxWidth: 760 }}>
      <h3>基本信息</h3>
      <Descriptions column={1} size="small" items={[
        { key: "code", label: "编码", children: <span className="cf-mono">{p.code}</span> },
        { key: "ds", label: "数据源", children: dss.find((d) => d.id === p.datasourceId)?.name || p.datasourceId },
        { key: "owner", label: "创建人", children: p.owner || "—" },
      ]} />
      <Form form={form} layout="vertical" style={{ marginTop: 12 }} onFinish={(v) => patch(`/api/pipelines/${p.id}`, v).then(() => { message.success("已保存"); reload(); }).catch((e) => message.error(e.message))}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input maxLength={128} /></Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea rows={3} maxLength={1000} /></Form.Item>
        <Button htmlType="submit" type="primary">保存</Button>
      </Form>
      <h3 style={{ marginTop: 24 }}>冻结</h3>
      <Space>
        {p.frozen ? <Tag color="purple">已冻结</Tag> : <Tag color="green">正常</Tag>}
        <Button onClick={() => toggleFreeze(p, message, reload)}>{p.frozen ? "解冻 🔒" : "冻结 🔒"}</Button>
      </Space>
      <p className="cf-muted">冻结期间调用方提交写表任务会收到「方案已冻结」。</p>
      <h3 style={{ marginTop: 24 }}>授权的调用方</h3>
      {clients.length ? clients.map((c) => <Tag key={c.id}>{c.name}（{c.appKey}）</Tag>) : <span className="cf-muted">尚无调用方被授权（在「调用方」页面管理）</span>}
    </div>
  );
}
