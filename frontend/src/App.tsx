import { Badge, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { get } from "./api";
import OpTokenModal from "./components/OpTokenModal";
import { getOperator, setOperator } from "./operator";
import Audit from "./pages/admin/Audit";
import Clients from "./pages/admin/Clients";
import Datasources from "./pages/admin/Datasources";
import SettingsPage from "./pages/admin/Settings";
import JobDetail from "./pages/JobDetail";
import Jobs from "./pages/Jobs";
import PipelineDetail from "./pages/PipelineDetail";
import Pipelines from "./pages/Pipelines";
import { navigate, useRoute } from "./router";

const NAV: [string, string][] = [
  ["pipelines", "方案"],
  ["jobs", "任务"],
  ["datasources", "数据源"],
  ["clients", "调用方"],
  ["settings", "系统设置"],
  ["audit", "审计日志"],
];

function OperatorBadge() {
  const [name, setName] = useState(getOperator());
  const [edit, setEdit] = useState(!getOperator());
  const [draft, setDraft] = useState(name);
  useEffect(() => {
    const on = () => setName(getOperator());
    window.addEventListener("cellflow-operator", on);
    return () => window.removeEventListener("cellflow-operator", on);
  }, []);
  return (
    <>
      <a id="operator-name" style={{ color: "#fff" }} onClick={() => { setDraft(name); setEdit(true); }}>
        操作人：{name || "未设置"}
      </a>
      <Modal title="设置操作人" open={edit} okText="保存" cancelText="取消" onCancel={() => setEdit(false)}
        onOk={() => { if (draft.trim()) { setOperator(draft.trim()); setEdit(false); } }}>
        <p className="cf-muted">v1 不做登录，操作人保存在本浏览器，用于审计记录；接入统一登录后自动替换。</p>
        <Input id="operator-input" value={draft} maxLength={64} onChange={(e) => setDraft(e.target.value)} placeholder="你的姓名" />
      </Modal>
    </>
  );
}

function PendingBadge() {
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => get<{ count: number }>("/api/jobs/pending-count").then((d) => alive && setN(d.count)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return <Badge count={n} size="small" offset={[6, -2]} />;
}

export default function App() {
  const route = useRoute();
  const top = route.path[0] || "pipelines";
  let page: JSX.Element;
  if (top === "pipelines" && route.path[1]) page = <PipelineDetail id={Number(route.path[1])} tab={route.path[2] || "canvas"} query={route.query} />;
  else if (top === "jobs" && route.path[1]) page = <JobDetail id={Number(route.path[1])} />;
  else if (top === "jobs") page = <Jobs query={route.query} />;
  else if (top === "datasources") page = <Datasources />;
  else if (top === "clients") page = <Clients />;
  else if (top === "settings") page = <SettingsPage />;
  else if (top === "audit") page = <Audit />;
  else page = <Pipelines />;
  return (
    <div>
      <div className="cf-header">
        <span className="logo">CellFlow</span>
        <span className="nav">
          {NAV.map(([k, t]) => (
            <a key={k} href={`#/${k}`} className={top === k ? "active" : ""} onClick={(e) => { e.preventDefault(); navigate(`/${k}`); }}>
              {t}
              {k === "jobs" && <PendingBadge />}
            </a>
          ))}
        </span>
        <span className="right">
          <span className="cf-intranet">仅内网</span>
          <OperatorBadge />
        </span>
      </div>
      {page}
      <OpTokenModal />
    </div>
  );
}
