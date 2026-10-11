# LeiKwanForward

LeiKwanForward是管理LeiKwan（会聚）专线转发规则的命令行客户端，支持Windows和Linux，可查询、创建、修改和删除转发，也可供脚本调用。

本项目旨在提供简洁易用、便于集成的基础CLI，命令交互与输出参考了[GitHub CLI](https://cli.github.com/manual/gh_api)。

安装包见[Releases](https://github.com/Mizuno-Sachiko/LeiKwanForward/releases)。

## 使用前准备

- 可用的LeiKwan专线服务。
- 专线API令牌和服务ID，在[账户安全](https://v3.leikwanhost.com/tenant/security)页面查看。

## 安装

### Windows

适用于Windows10/11 x64，安装包自带Python3.14.8。解压`LeiKwanForward-windows-x64.zip`，在安装用户的管理员PowerShell中进入解压后的`LeiKwanForward`目录，执行：

```powershell
.\install-windows-forward.ps1
```

默认安装到`$env:ProgramFiles/LeiKwanForward`。指定其他目录：

```powershell
.\install-windows-forward.ps1 -InstallDir "D:\Program Files\LeiKwanForward"
```

添加`-WhatIf`可预览安装。

安装脚本将安装目录加入系统PATH。重新打开普通终端，在任意目录执行：

```powershell
LeiKwanForward --help
```

### Linux

需要系统Python3.12或以上。解压`LeiKwanForward-linux.tar.gz`，在终端中进入解压后的`LeiKwanForward`目录，执行：

```bash
sudo python3 install-linux-forward.py
```

默认安装到`/opt/LeiKwanForward`。指定其他目录：

```bash
sudo python3 install-linux-forward.py --install-dir /opt/apps/LeiKwanForward
```

添加`--dry-run`可预览安装。安装脚本创建`/usr/local/bin/LeiKwanForward`命令，该目录需在PATH中。

安装后，以执行sudo的用户在任意目录运行；以root安装时使用root运行：

```bash
LeiKwanForward --help
```

## 使用

命令中的ID均为示例，请替换为实际值。需要注意：服务ID与官方客户端ID不同。

创建、修改和删除默认显示预览，输入`y`确认后执行。

### 登录

保存API令牌：

```text
LeiKwanForward login "YOUR_API_TOKEN"
```

省略令牌时，程序会提示隐藏输入：

```text
LeiKwanForward login
```

传入令牌会覆盖已保存的令牌；隐藏输入时，覆盖行为需经过确认。

保存后验证令牌及服务访问权限：

```text
LeiKwanForward status --service 42
```

### 查询服务、线路和转发

| 命令 | 用途 |
|---|---|
| `LeiKwanForward services` | 列出账号下的服务及服务ID |
| `LeiKwanForward routes --service 42` | 查看可用线路、传输方式和配额 |
| `LeiKwanForward list --service 42` | 列出该服务的生效转发 |
| `LeiKwanForward show 123` | 查看单条转发的目标、端口和线路 |

### 创建转发

在官方客户端所在设备上监听`11024`端口，将TCP和UDP流量转发到`example.com:443`：

```text
LeiKwanForward create --service 42 --route 14 --listen 11024 --host example.com --port 443
```

- `--service`：所属服务ID。
- `--route`：可用线路ID。
- `--listen`：客户端设备上的监听端口，省略或填写`auto`时由平台分配。
- `--host`和`--port`：目标IP或域名及端口。
- `--transport`：`tcp`、`udp`或`tcp+udp`，默认`tcp+udp`。

普通转发的目标须为公网IP或域名。部分线路仅允许指定IP段内的目标。

目标也可以是自己名下具有公网IP的另一个服务：

```text
LeiKwanForward create --service 42 --route 14 --target-service 43 --port 8443
```

套餐和线路支持Native Outbound（设备侧出站）时：

```text
LeiKwanForward create --service 42 --route 14 --native-outbound
```

这类转发要求`tcp+udp`，无需指定目标。

Native Outbound和转发到另一个服务的流程尚未实测：
因为维护者的套餐未开启Native Outbound，也没有具有公网IP的自有目标服务。

### 修改转发

只修改指定字段。例如，修改目标地址和端口：

```text
LeiKwanForward edit 123 --host backend.example.com --port 8443
```

修改线路、监听端口或传输方式：

```text
LeiKwanForward edit 123 --route 14
LeiKwanForward edit 123 --listen auto
LeiKwanForward edit 123 --transport tcp
```

将普通转发改为Native Outbound：

```text
LeiKwanForward edit 123 --native-outbound --transport tcp+udp
```

关闭Native Outbound并设置普通转发目标：

```text
LeiKwanForward edit 123 --no-native-outbound --host example.com --port 443
```

修改规则会重新建立转发，正在使用的连接可能中断。

### 删除转发

```text
LeiKwanForward delete 123
```

每个服务最多保存512条转发记录，包含生效和已删除的规则；达到上限后无法继续创建，需联系客服清理。所以请不要反复创建然后删除规则。

### 查看帮助

```text
LeiKwanForward
LeiKwanForward --help
LeiKwanForward create --help
LeiKwanForward edit -h
```

无参数仅显示命令概览。

## 凭据与更新

凭据保存在安装目录下的`data/api-token`。Windows使用安装用户的加密保护，通常只能在原电脑由原用户解密；Linux以明文保存，目录权限为0700、文件权限为0600，仅所有者可读写。

移除本地保存的令牌：

```text
LeiKwanForward logout
```

仅删除本地令牌，已有转发保持原样。

更新时解压新安装包，重新执行安装脚本并指定原安装目录，程序会保留已有凭据。

## 脚本调用

标准输出返回成功JSON或HTTP错误正文；标准错误输出用于预览、确认和诊断。登录、退出成功及取消操作无结果输出。

脚本应检查退出码：成功`0`，请求或文件错误`1`，参数错误`2`，Ctrl+C中断`130`。

使用`-y`免交互执行：

```powershell
LeiKwanForward list --service 42 | ConvertFrom-Json
LeiKwanForward edit 123 --port 8443 -y | ConvertFrom-Json
```

删除成功会返回`{"ok": true, "deleted_id": 123}`。

API平台按账号限流：新旧接口合计每分钟最多120次请求，其中POST、PATCH、DELETE最多30次。超限则返回`429 rate_limited`，响应头`Retry-After`会给出等待秒数。

CLI不会自动重试。创建请求中断后平台仍可能完成操作；重试时请使用错误输出中的`Idempotency-Key`作为`--key`，其余参数保持原样。

将`leikwan_api.py`放入Python项目即可调用：

```python
from leikwan_api import Client

client = Client("YOUR_API_TOKEN")
result, etag = client.request("GET", "forwards/123")
```

`request()`返回JSON和规则版本ETag；修改、删除时通过`etag=`传入版本。

失败抛出`APIError`：`status`为HTTP状态，`body`为原始正文，`result`为解析后的JSON，`headers`为响应头。可用`headers.get("Retry-After")`等方式读取，名称不区分大小写。

使用安装目录中的模块时，可用`Client.from_saved_token()`读取该安装保存的令牌。

## 参考资料

- [LeiKwan Bridge官方知识库](https://v3.leikwanhost.com/knowledgebase)
- [Python嵌入式运行环境](https://docs.python.org/3.14/using/windows.html#the-embeddable-package)
- [Python许可](https://docs.python.org/3.14/license.html)
