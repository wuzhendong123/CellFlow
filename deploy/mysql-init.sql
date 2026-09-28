-- 本地开发初始化：元数据库、模拟业务库、开发账号（口令为占位值）
CREATE DATABASE IF NOT EXISTS cellflow_meta CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS cellflow_biz CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'cellflow'@'%' IDENTIFIED WITH mysql_native_password BY 'change_me';
GRANT ALL ON cellflow_meta.* TO 'cellflow'@'%';
GRANT ALL ON cellflow_biz.* TO 'cellflow'@'%';
-- 测试会话会创建 cellflow_test_* 临时库
GRANT CREATE, DROP ON *.* TO 'cellflow'@'%';
GRANT ALL ON `cellflow_test%`.* TO 'cellflow'@'%';
