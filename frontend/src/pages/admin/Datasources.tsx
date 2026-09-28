import { Alert, App, Button, Form, Input, Modal, Space, Table, Tag } from "antd";
import { useEffect, useState } from "react";
import { get, opCall, post } from "../../api";

/** P9 数据源管理（F15）：只填环境变量引用名，页面不出现真实地址和密码。 */
export default function Datasources() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [edit, setEdit] = useState<any>(null);
  const [tests, setTests] = useState<Record<number, any>>({});
  const [form] = Form.useForm();
  const load = () => get<any[]>("/api/datasources").then(setRows);
  useEffect(() => { load(); }, []);
  const open = (d: any) => { setEdit(d); form.setFieldsValue(d.id ? d : { name: "", dbName: "", hostRef: "", credentialRef: "" }); };
  const save = async () => {
    const v = await form.validateFields();
    const r = await opCall({ title: edit.id ? `修改数据源 ${edit.name}` : "新建数据源", summary: `名称 ${v.name}，库 ${v.dbName}，连接引用 ${v.hostRef}，凭证引用 ${v.credentialRef || "—"}` },
      edit.id ? "PUT" : "POST", edit.id ? `/api/datasources/${edit.id}` : "/api/datasources", () => v).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success("已保存"); setEdit(null); load(); }
  };
  const del = async (d: any) => {
    const r = await opCall({ title: `删除数据源 ${d.name}`, summary: "删除后不可恢复（仅在没有方案使用时可删）。" }, "DELETE", `/api/datasources/${d.id}`)
      .then(() => true).catch((e) => { message.error(e.message); return null; });
    if (r) { message.success("已删除"); load(); }
  };
  const test = (d: any) => post(`/api/datasources/${d.id}/test`).then((r) => setTests({ ...tests, [d.id]: r })).catch((e) => message.error(e.message));
  return (
    <div className="cf-page">
      <Button type="primary" style={{ marginBottom: 12 }} id="new-datasource" onClick={() => open({})}>+ 新建数据源</Button>
      <Table rowKey="id" dataSource={rows} pagination={false} columns={[
        { title: "名称", dataIndex: "name" }, { title: "库名", dataIndex: "dbName" },
        { title: "连接引用名", dataIndex: "hostRef", render: (v) => <span className="cf-mono">{v}</span> },
        { title: "凭证引用名", dataIndex: "credentialRef", render: (v) => <span className="cf-mono">{v || "—"}</span> },
        { title: "使用方案数", dataIndex: "pipelineCount" },
        { title: "连通性", render: (_: any, d: any) => {
          const t = tests[d.id];
          if (!t) return "—";
          return <Space size={2} wrap>{Object.entries<boolean>(t.checks).map(([k, ok]) => <Tag key={k} color={ok ? "green" : "red"} title={t.detail?.[k]}>{k}</Tag>)}</Space>;
        } },
        { title: "操作", render: (_: any, d: any) => <Space><a onClick={() => test(d)}>测试连接</a><a onClick={() => open(d)}>编辑 🔒</a><a className="cf-err" onClick={() => del(d)}>删除 🔒</a></Space> },
      ]} />
      <Modal open={!!edit} title={edit?.id ? "编辑数据源" : "新建数据源"} onCancel={() => setEdit(null)} onOk={save} okText="保存… 🔒" cancelText="取消">
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="只填写环境变量 / 密钥管理中的名称（如 BIZ_DB_URL），真实地址与密码不在页面出现。服务端读取 CF_REF_<名称>。" />
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
          <Form.Item name="dbName" label="库名" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
          <Form.Item name="hostRef" label="连接地址引用名" rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]*$/, message: "大写字母、数字、下划线" }]}><Input className="cf-mono" /></Form.Item>
          <Form.Item name="credentialRef" label="凭证引用名" rules={[{ pattern: /^[A-Z][A-Z0-9_]*$/, message: "大写字母、数字、下划线" }]}><Input className="cf-mono" /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
