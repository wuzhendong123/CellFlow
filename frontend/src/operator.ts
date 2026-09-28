// v1 无登录（D19/W5）：操作人姓名保存在浏览器本地，高危操作时预填
const KEY = "cellflow.operator";

export function getOperator(): string {
  try {
    return localStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
}

export function setOperator(name: string): void {
  try {
    localStorage.setItem(KEY, name);
  } catch {
    /* 隐私模式下忽略 */
  }
  window.dispatchEvent(new Event("cellflow-operator"));
}
