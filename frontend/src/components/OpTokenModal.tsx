import { Alert, Form, Input, Modal } from "antd";
import { useEffect, useState } from "react";
import { OpAnswer, OpRequest, registerOpAsker } from "../api";
import { getOperator, setOperator } from "../operator";

/** 操作口令弹窗（WIREFRAME §3.2，F16）：所有高危操作共用。 */
export default function OpTokenModal() {
  const [req, setReq] = useState<OpRequest | null>(null);
  const [resolver, setResolver] = useState<((a: OpAnswer | null) => void) | null>(null);
  const [form] = Form.useForm();

  useEffect(() => {
    registerOpAsker(
      (r) =>
        new Promise((resolve) => {
          setReq(r);
          setResolver(() => resolve);
          form.setFieldsValue({ operator: getOperator(), token: "", reason: form.getFieldValue("reason") || "" });
        }),
    );
  }, [form]);

  const close = (a: OpAnswer | null) => {
    resolver?.(a);
    setReq(null);
    setResolver(null);
  };

  return (
    <Modal
      open={!!req}
      title={req?.title}
      okText="确认"
      cancelText="取消"
      okButtonProps={{ danger: true, id: "op-confirm" }}
      onCancel={() => close(null)}
      onOk={async () => {
        const v = await form.validateFields();
        if (v.operator !== getOperator()) setOperator(v.operator);
        close({ token: v.token, operator: v.operator, reason: v.reason || "" });
        form.setFieldValue("reason", "");
      }}
      destroyOnHidden
    >
      <p style={{ whiteSpace: "pre-wrap" }}>{req?.summary}</p>
      {req?.danger && <Alert type="error" showIcon message={req.danger} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="operator" label="操作人" rules={[{ required: true, message: "请填写操作人" }]}>
          <Input id="op-operator" maxLength={64} />
        </Form.Item>
        <Form.Item name="reason" label="理由" rules={req?.reasonRequired ? [{ required: true, message: "请填写理由" }] : []}>
          <Input.TextArea id="op-reason" rows={2} maxLength={500} />
        </Form.Item>
        <Form.Item name="token" label="操作口令" rules={[{ required: true, message: "请输入操作口令" }]}>
          <Input.Password id="op-token" autoComplete="off" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
