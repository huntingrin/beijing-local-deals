# 北京吃喝玩乐优惠 · beijing-local-deals

输入需求，Agent实时查找北京餐饮、饮品、外卖、演出电影、展览景区优惠，交付**带具体条件说明的二维码**。支持网页、小红书、微信公众号三路信息，并如实报告缺失/受阻渠道。

**这是跨Agent的Skill和辅助工具，不是独立运行的优惠搜索服务。** Agent负责调用环境中的搜索/MCP、理解需求和核验规则；Python工具负责公众号公开索引搜索/取文、格式与日期检查、二维码和报告。不会自动领券、关注、发消息、下单、预约或支付。

## 安装与使用

把仓库放入你的Agent支持的技能目录，目录名保持`beijing-local-deals`。支持读取SKILL.md的Agent可直接使用；其他Agent可以先读取该文件，不需要特定厂商API。

```sh
git clone https://github.com/huntingrin/beijing-local-deals.git
cd beijing-local-deals
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

建议Python 3.12或以上；Windows使用`.venv\Scripts\python.exe`。依赖按范围约束，当前验证环境和边界见[验证记录](references/validation.md)。原生二维码库在没有预编译轮子的系统上可能需要编译环境；不要为此擅自修改系统环境。

例如Codex可将仓库目录放到`~/.codex/skills/beijing-local-deals`；已经在别处克隆时可建立该目录的符号链接。其他宿主按其技能安装机制处理，不宣称所有Agent会自动发现技能。

向Agent输入：

> 使用 beijing-local-deals，找北京朝阳区这周末两个人吃饭的优惠，人均100以内。先搜索最新结果，每个二维码说明实际优惠、门槛和适用门店。

> 找北京今天咖啡奶茶优惠，微信公众号文章入口也可以，给我带说明的二维码。

## 工作流程

1. 根据需求生成当次查询组合，检查当前北京时间和工具能力。
2. 网页查规则，小红书查线索/体验，公众号读具体原文；记录真实渠道状态。
3. 筛选适用北京、活动期匹配、优惠可解释的条目。转载不重复算独立证据。
4. 将证据写入`offers.json`；不合格项放`leads`，不凑数。
5. 生成报告、说明卡、原始二维码和逐项文字说明；在本机TUI场景实际打开图片。

```sh
.venv/bin/python scripts/deals.py init --request '北京周末观演优惠' --out runs/my-request
# Agent完成本次搜索后填充 runs/my-request/offers.json
.venv/bin/python scripts/deals.py build --input runs/my-request/offers.json --out runs/my-delivery --open
```

`--open`会用系统默认应用打开带二维码的说明卡（无中文字体时打开二维码原图，说明保存在TXT/HTML）；不传则后台生成。macOS用`open`，Windows用系统关联，Linux有桌面会话时用`xdg-open`。远程/无GUI不假装已经展示，应把图片作为可下载文件交付。可用`--font /path/to/CJK-font.ttc`指定本机中文字体；不打包商业字体。

每个结果包括：优惠金额/价格、北京范围、活动使用期、领取截止、限制、扫码后步骤、规则/入口/领取三项状态、佣金关系、来源和本次检查时间。JSON schema在[references/output-schema.json](references/output-schema.json)。脚本不能替Agent判断事实、账号身份或门店资格。

### 三种码

| 类型 | 扫码用途 | 不能据此承诺 |
|---|---|---|
| 文章入口码 | 打开已读过的微信公众号原文 | 不是扫码即领，也不保证原文每个项目都有效 |
| 活动入口码 | 打开原始活动H5 | 页面响应不代表账号有资格或库存 |
| 原始小程序码 | 打开来源提供的小程序入口 | 通用解码器通常不能反解，不编造AppID/路径码 |

普通二维码生成后会反解比对；原始图片另存并记录SHA256。图片说明卡上同时印出用途、优惠和限制。没有中文字体也会生成完整HTML、TXT和可扫码原图。

## 微信公众号适配器

```sh
.venv/bin/python scripts/weixin.py search '北京 咖啡 优惠 当前年月' --out runs/weixin-search --limit 3
.venv/bin/python scripts/weixin.py article 'https://mp.weixin.qq.com/s/实际原文地址' --out runs/weixin-article
```

搜索来源是**搜狗收录的公众号文章**，不是微信客户端搜一搜。搜索结果可能不全；日期从原文与条款核对。只处理公开HTTP(S)内容，保留匿名搜索会话，不使用用户微信cookie。输出正文、图片URL、外链和小程序标签，由Agent继续核实。

验证/限流时退出码2，保留候选与证据，并写持久停止标记。后续运行在请求前停止。默认状态目录`~/.local/state/beijing-local-deals`，宿主可统一设置`BEIJING_DEALS_STATE_DIR`；**不得换目录绕开已知停止记录**。

```sh
# 迁移另一个研究流程已发现的阻塞状态，不清除原始记录
.venv/bin/python scripts/weixin.py stop --reason '前序公众号研究遇验证，尚未解除'
# 离线读取本地证据，不访问网络
.venv/bin/python scripts/weixin.py parse --kind article --html saved.html --url 'https://mp.weixin.qq.com/s/实际原文地址' --out parsed.json
```

仅在用户通过正常流程处理验证并明确确认后，才人工归档停止记录并低频复测。没有自动恢复/代理轮换/验证码处理。更换CLI不算新增独立渠道，也不能用于绕过同一目标阻塞。

## 示例和测试

仓库示例是**虚构格式样本**，其二维码指向example.com，不是可领优惠：

```sh
.venv/bin/python scripts/deals.py build --input examples/offers.json --out runs/example --historical
.venv/bin/python -m unittest discover -s tests -v
```

`--historical`用于旧记录/示例回放，输出显著标“非当前推荐”。不传此选项时，旧检查日期、过期活动、过期领取截止、仅搜索摘要和无证据的领取成功等会拒绝生成。

运行结果、账号信息、Cookie、本机路径和原始抓取内容不发布到仓库。MIT许可；公众号解析参考的上游归属和许可证见[third_party/NOTICE.txt](third_party/NOTICE.txt)。
