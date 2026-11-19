# 华为 ICT 大赛创新赛：AI 实时手语识别与双向无障碍翻译平台
## —— 开源调研、系统设计思路与华为云全栈技术路线方案

> **项目定位**：基于昇思 MindSpore、昇腾 Ascend、华为云 ModelArts、盘古大模型（MaaS）及语音交互服务（SIS），构建集**“实时摄像头手语捕获翻译 + 语音反向驱动 3D 数字人打手语 + AI 手语互动教学纠错”**于一体的端边云协同无障碍交流平台。

---

## 一、 GitHub 核心开源项目与可利用知识库清单

做实时摄像头手语识别翻译，切忌直接用原始 RGB 视频流跑重型 3D CNN/Video ViT（推理延迟高，且极易受答辩现场光照、背景干扰）。工程落地最稳、实时性最高的路线是：**“3D 骨骼关键点提取 + 时空图卷积/Transformer 序列识别 + 大语言模型（LLM）语义重构”**。

### 1. 实时摄像头捕获与工程脚手架（端侧/前端直接复用）

| 项目名称与链接 | 技术栈 | 核心价值与可复用模块 |
| :--- | :--- | :--- |
| **[nicknochnack/ActionDetectionforSignLanguage](https://github.com/nicknochnack/ActionDetectionforSignLanguage)** | OpenCV, MediaPipe Holistic, LSTM/Sequence | **最经典实时摄像头手语检测脚手架**。可直接复用其摄像头视频流捕获、**人体姿态（33点）+ 双手（21×2点）+ 面部表情（468点）**提取、滑动窗口序列缓存（Sliding Window）及自定义手语词库快速录制脚本。 |
| **[tobp03/live-asl-translation](https://github.com/tobp03/live-asl-translation)** | MediaPipe, GRU/Transformer, LLM API | **打通“手语词元（Gloss）$\rightarrow$ 大模型自然成句”的实时流水线**。展示了如何将视觉模型识别出的离散、倒装手语词根流式送入 LLM，实时润色为符合自然语法的通顺句子。 |
| **[sign-language-translator/sign-language-translator](https://github.com/sign-language-translator/sign-language-translator)** | Python, NLP, Landmarks, 3D Avatar | **模块化双向手语翻译框架**。提供手语识别（Sign-to-Text）与手语合成（Text-to-Sign 骨骼动画拼接）的完整工程代码组织参考。 |

### 2. 中文手语（CSL）前沿算法与权威数据集（模型训练核心）

| 项目/资源名称与链接 | 机构/来源 | 核心价值与可复用模块 |
| :--- | :--- | :--- |
| **[FangyunWei/SLRT](https://github.com/FangyunWei/SLRT)** | 微软亚洲研究院 | **连续手语识别（CSLR）与翻译（SLT）顶会标杆库**（含 `Two-Stream-SLR`）。原生支持中文手语数据集 **CSL-Daily**，其骨骼构图（Graph Construction）与 CTC Loss 序列对齐逻辑可直接迁移。 |
| **[hulianyuyy/CorrNet_Plus](https://github.com/hulianyuyy/CorrNet_Plus)** | 学术前沿开源 | 高性能连续手语识别与翻译框架，提供针对 **CSL-Daily** 的数据预处理脚本与时空关联特征提取模块。 |
| **[zhoubenjia/Uni-Sign](https://github.com/zhoubenjia/Uni-Sign)** | 中科院计算所 VIPL | 大规模统一手语理解与翻译架构，包含手部精细姿态、身体躯干与面部语法表情的多模态特征融合方案。 |
| **[VIPL-SLP/Awesome-Sign-Language-Processing](https://github.com/VIPL-SLP/Awesome-Sign-Language-Processing)** | 中科院计算所 VIPL | **中文手语知识库大全**。汇总了国内权威中文手语数据集申请入口与 Baseline：**CSL-Daily**（中科大开源，2万+日常中文手语句子，含词级 Gloss 与汉语句子标注）、**USTC-CSL / DEVISIGN**（孤立词数据集）。 |

### 3. 华为生态（昇思 MindSpore + ModelArts + 昇腾）迁移参考

| 项目名称与链接 | 核心价值与可复用模块 |
| :--- | :--- |
| **[mindspore-ai/models](https://github.com/mindspore-ai/models)** | **昇思 MindSpore 官方模型库**。直接复用 `official/cv/stgcn`（时空图卷积网络）与 `Transformer` 的 MindSpore 原生算子实现，修改输入通道即可在华为云 ModelArts 昇腾 910 算力上训练，满足大赛对华为 AI 根技术的硬性要求。 |
| **[xiangwentao666/MindSpore_ModelArts_OpenPose_SKP_Based_HAR](https://github.com/xiangwentao666/MindSpore_ModelArts_OpenPose_SKP_Based_HAR)** | **ModelArts + MindSpore 骨骼关键点动作识别全流程参考**。完整跑通了华为云 OBS 数据桶挂载、ModelArts 云端训练作业提交、`.ckpt`/`.mindir` 模型导出与推理流程。 |

---

## 二、 核心设计思路：工程破局点与大赛拿奖亮点

针对华为 ICT 大赛创新赛评审标准（**含华量、技术创新性、实时交互体验、社会与商业价值**），本方案采用 **“三阶段解耦实时架构 + 双向无障碍闭环”** 设计：

### 1. 端侧骨骼化抽象（解决“实时性”与“现场环境干扰”痛点）
* **不直接传输原始视频流做云端重型推理**：摄像头以 30 FPS 捕获画面后，在端侧（浏览器 WebAssembly / 边缘端 / 客户端）实时提取 **人体上半身 + 左右手各 21 个 3D 关键点 + 面部核心表情点（共 75 个关键点）**。
* **核心收益**：
  1. 单帧数据量从数 MB 压缩至 `< 1 KB` 的坐标张量 $(75 \times 3)$，通过 WebSocket 流式传输延迟低于 `10 ms`；
  2. **100% 免疫比赛答辩现场复杂背景、强弱光照及演示者衣物颜色干扰**；
  3. 仅传输骨骼点坐标，天然实现用户面部隐私保护。

### 2. 运动能量 VAD + 时空图卷积（解决“连续手语动作切分”痛点）
* 真实手语交流是连续流，包含抬手、停顿、放手等过渡帧。
* 引入类似语音端点检测（VAD）的 **“手部运动能量阈值检测”**：实时计算相邻帧手腕与指尖关键点的欧氏位移速率，自动判定手势起始与结束边界，过滤静止帧，再将有效序列送入基于 **昇思 MindSpore** 构建的 **Dual-Stream ST-GCN + Transformer** 网络（同时输入关节坐标流 Joint Stream 与骨骼向量流 Bone Stream），输出带置信度的手语词元序列（Glosses）。

### 3. 大小模型协同：视觉小模型 + 华为云大模型 MaaS（解决“手语语序倒装生硬”痛点）
* **为什么需要大模型？** 中国自然手语语法常采用宾语前置、时间前置、省略虚词（例如手语打出的词序为：`["昨天", "药", "吃", "没有", "头晕"]`），传统分类模型只能蹦出孤立词，无法称之为“翻译”。
* **协同机制**：前端视觉小模型（MindSpore ST-GCN）负责毫秒级精准识别手语词元（Gloss）；后端调用 **华为云盘古大模型 / ModelArts Studio (MaaS)**，结合当前场景（政务大厅、医院问诊、日常交流）及上下文记忆，将离散词元实时重构为自然、得体的中文口语（“医生您好，我昨天忘记吃药了，现在感觉有点头晕”），并通过 **华为云语音交互服务（SIS）** 实时播报。

### 4. 赛题升华：构建“双向无障碍交流 + AI 手语教学”闭环
* **正向通道（听障人 $\rightarrow$ 健听人）**：摄像头实时捕捉手语 $\rightarrow$ MindSpore 识别词元 $\rightarrow$ 华为云 MaaS 润色成句 $\rightarrow$ 华为云 SIS 情感语音合成播报。
* **反向通道（健听人 $\rightarrow$ 听障人）**：健听人语音输入 $\rightarrow$ 华为云 SIS 语音识别（ASR） $\rightarrow$ 大模型转换为中国手语语法词序 $\rightarrow$ **驱动 3D 手语数字人（Three.js / 华为云 MetaStudio）** 实时做出手语动作。
* **公益扩展（AI 手语跟练与纠错）**：内置标准手语教学库，用户对着摄像头跟练，系统通过计算用户实时骨骼向量与标准动作库向量的时空余弦相似度（DTW + Cosine Similarity）进行实时打分与指姿纠错。

---

## 三、 整体技术架构与华为云产品矩阵映射

### 1. 端-边-云协同系统架构图

```mermaid
flowchart TD
    subgraph Client["端侧与交互层 (Web平台 / 鸿蒙App / 政务医疗自助终端)"]
        Cam["实时摄像头捕获 (WebRTC / OpenCV 30FPS)"] --> Pose["轻量化骨骼关键点提取与空间归一化<br/>(双手42点 + 姿态15点 + 面部18点)"]
        Pose -->|"75×3 关键点张量流 (WebSocket)"| Gateway
        UI["可视化交互界面 (实时骨骼叠加渲染)"]
        AudioOut["实时语音播报与双语字幕"]
        Avatar["3D手语虚拟数字人 (Three.js / MetaStudio)"]
    end

    subgraph Server["实时推理与业务网关层 (华为云 ECS / 昇腾 Atlas 200I DK A2)"]
        Gateway["FastAPI 异步流式网关"] --> VAD["运动能量检测 (动作端点切分 & 滑窗缓存)"]
        VAD --> MS_Infer["昇思 MindSpore Lite / MindIE 推理引擎<br/>(Dual-Stream ST-GCN + Transformer)"]
        MS_Infer -->|"输出 Top-K 手语词元序列 (Glosses)"| Orchestrator["多模态翻译编排器"]
    end

    subgraph HuaweiCloud["华为云全栈 AI 底座 (核心含华量)"]
        ModelArts["华为云 ModelArts + 昇腾910集群<br/>(CSL-Daily与自建数据集分布式训练)"]
        OBS["华为云对象存储 OBS<br/>(数据集托管 / MindIR模型仓库 / 增量样本回流)"]
        MaaS["华为云 MaaS / 盘古NLP大模型<br/>(Gloss语序重构 / 医疗政务RAG润色 / 反向手语语法拆解)"]
        SIS["华为云语音交互服务 SIS<br/>(TTS 低时延语音合成 / ASR 实时语音识别)"]
        RDS["华为云 RDS + GeminiDB<br/>(国家通用手语词典库 / 3D骨骼动画库 / 业务日志)"]
    end

    ModelArts <--> OBS
    ModelArts -->|"导出 .mindir / CANN编译 .om"| MS_Infer
    Orchestrator <-->|"流式 Prompt 语义重构"| MaaS
    Orchestrator <-->|"TTS合成 / ASR识别"| SIS
    Orchestrator <--> RDS
    Orchestrator -->|"自然汉语句子 + 音频流"| AudioOut
    Orchestrator -->|"手语词序列骨骼动画帧"| Avatar
```

### 2. 华为云核心技术栈选型表（计划书与答辩核心得分项）

| 技术领域 | 选用华为产品/根技术 | 在本系统中的具体应用场景 |
| :--- | :--- | :--- |
| **AI 算力底座** | **昇腾 Ascend 910 / 310 + CANN** | 云端依托昇腾 910 训练大规模连续手语模型；推理侧利用 CANN 工具链将模型转为离线 `.om` 格式，发挥异构算子加速能力。 |
| **AI 框架** | **昇思 MindSpore 2.x & MindSpore Lite** | 基于 `mindspore.nn` 原生构建双流 ST-GCN + Transformer 模型与 CTC 损失函数；利用 MindSpore Lite 实现端/云毫秒级推理。 |
| **一站式开发平台** | **华为云 ModelArts + 对象存储 OBS** | 使用 OBS 存放手语骨骼序列数据集，利用 ModelArts 进行云端训练作业管理、可视化评估及训练日志追踪。 |
| **大模型服务** | **华为云盘古大模型 / ModelArts Studio (MaaS)** | 核心翻译大脑：1. 将碎片化手语 Gloss 重构为通顺中文；2. 结合政务/医疗术语库做场景化表达修正；3. 将健听人汉语句子拆解为手语词序。 |
| **智能语音交互** | **华为云语音交互服务 (SIS)** | 调用 **实时语音合成（TTS）** 为听障人士提供男/女声情感播报；调用 **实时语音识别（ASR）** 捕获健听人语音输入。 |
| **云基础设施** | **华为云弹性云服务器 (ECS) + 云数据库 RDS** | 承载 FastAPI 后端服务、WebSocket 实时流媒体服务、手语标准词典库及 3D 数字人动作姿态库。 |

---

## 四、 实时摄像头手语捕获与翻译核心算法流水线（Pipeline 详解）

### Step 1：关键点拓扑提取与人体空间归一化（消除距离与位置偏差）
摄像头每捕获一帧画面，提取 $N=75$ 个核心关键点（左手 21 点 + 右手 21 点 + 上半身躯干 15 点 + 面部眉眼嘴核心语法表情点 18 点），每个点包含 $(x_i, y_i, z_i, c_i)$。

为消除演示者站位远近、身高差异及画面偏移，在送入模型前执行**以双肩中心为原点的空间归一化**：
1. 计算参考中心点（双肩中点）：
   $$P_{\text{center}} = \frac{P_{\text{left\_shoulder}} + P_{\text{right\_shoulder}}}{2}$$
2. 计算人体尺度基准（双肩欧氏距离）：
   $$S = \|P_{\text{left\_shoulder}} - P_{\text{right\_shoulder}}\|_2 + \epsilon$$
3. 对所有关键点坐标进行中心化与尺度归一化（手部关键点同时保留以各自手腕 $P_{\text{wrist}}$ 为原点的局部相对坐标，增强手指精细动作表征）：
   $$\hat{P}_i = \frac{P_i - P_{\text{center}}}{S}, \quad \hat{P}_{i,\text{hand\_local}} = \frac{P_{i,\text{hand}} - P_{\text{wrist}}}{S_{\text{palm}}}$$

### Step 2：构造关节流（Joint）与骨骼流（Bone）双流特征
单靠坐标点难以充分表达手指弯曲角度，我们在预处理层同时计算二阶特征：
* **关节位置流（Joint Stream）**：$\mathcal{J}_t = \{\hat{P}_i^{(t)}\}$
* **骨骼向量流（Bone Stream）**：沿着人体自然骨骼连接边 $(i, j) \in \mathcal{E}$，计算向量 $\mathcal{B}_t = \{\hat{P}_i^{(t)} - \hat{P}_j^{(t)}\}$
* **帧间运动速度流（Motion Velocity）**：$\mathcal{M}_t = \hat{P}_i^{(t)} - \hat{P}_i^{(t-1)}$

### Step 3：动态运动能量端点检测（Gesture VAD）与滑动窗口
为实现自然流畅的实时摄像头翻译，维护一个最大长度 $T=64$ 帧的滑动缓冲区，并计算实时手部运动能量：
$$E_t = \frac{1}{|H|} \sum_{k \in H} \|\hat{P}_k^{(t)} - \hat{P}_k^{(t-1)}\|_2$$
* **激活状态（Active Signing）**：当连续 3 帧 $E_t > \tau_{\text{start}}$ 且双手高于腰部基准线时，标记手语动作开始，每隔步长 $s=8$ 帧触发一次增量词元推理；
* **成句触发（Sentence Commit）**：当检测到双手自然垂落或连续 15 帧（约 0.5 秒）$E_t < \tau_{\text{stop}}$ 时，判定当前手语句子输入完毕，立即将缓冲区累积的词元序列提交给华为云大模型成句。

### Step 4：基于昇思 MindSpore 的 ST-GCN + Transformer 模型推理
模型输入形状为 `(Batch, Channels=9, Frames=T, Vertices=75)`（Channels 融合坐标、骨骼向量与运动速度）：
1. **Spatial Graph Convolution（空间图卷积层）**：利用预定义的 75 点人体+双手拓扑邻接矩阵 $A \in \mathbb{R}^{75 \times 75}$ 及可学习的自适应注意力掩码 $M_{\text{att}}$，聚合单帧内手型、手位与表情关联特征。
2. **Multi-Scale Temporal Convolution（多尺度时序卷积）**：通过不同膨胀率（Dilation = 1, 2, 4）的 1D 时序卷积捕获快慢不同的打手语节奏。
3. **Transformer Contextual Encoder + CTC / Classification Head**：输出当前窗口的 Top-3 候选词元及其概率分布，例如：
   ```json
   [
     {"step": 1, "candidates": [{"gloss": "我", "prob": 0.98}]},
     {"step": 2, "candidates": [{"gloss": "身份证", "prob": 0.93}, {"gloss": "银行卡", "prob": 0.05}]},
     {"step": 3, "candidates": [{"gloss": "丢失", "prob": 0.91}, {"gloss": "忘记", "prob": 0.06}]},
     {"step": 4, "candidates": [{"gloss": "补办", "prob": 0.95}]}
   ]
   ```

### Step 5：华为云大模型（MaaS）语义重构 Prompt 工程设计
后端将上述带置信度的多候选 Gloss JSON 送入华为云 MaaS 接口，利用大模型强大的上下文推理能力消除局部识别歧义（例如结合“补办”自动高置信选择“身份证”而非不相关词汇）：

```markdown
# Role
你是一个部署在华为云上的专业中国手语（CSL）实时同声传译专家系统。

# Context
- 当前应用场所：{{scene_mode}}（可选：政务服务大厅 / 医院门诊部 / 银行柜台 / 日常社交）
- 历史对话上文：{{chat_history}}

# Input
视觉模型（MindSpore ST-GCN）实时捕获的连续手语词元序列（按时间顺序排列，含Top候选词与置信度）及面部表情标签（如：疑问眉、焦急、肯定点头）：
{{gloss_sequence_json}}

# Rules
1. 中国自然手语常采用“话题-评论”结构（宾语前置、时间状语前置、否定词后置、省略介词与助词）。
2. 请综合候选词置信度与当前场所语境，纠正可能的同义手势歧义，将其重组为一句自然、得体、符合现代汉语习惯的第一人称口语。
3. 严禁编造输入词元中完全不存在的核心事实；只输出最终翻译后的汉语句子，不要输出任何解释说明。
```

---

## 五、 工程代码目录结构设计（推荐落地方案）

后续在本地工作区启动开发时，建议采用如下清晰的模块化工程结构：

```text
ict-sign-language-platform/
├── frontend/                        # 前端交互平台 (Vue3 + TypeScript + Vite)
│   ├── src/
│   │   ├── components/
│   │   │   ├── CameraCapture.vue    # WebRTC 摄像头捕获 + 实时骨骼 Canvas 渲染层
│   │   │   ├── TranslationPanel.vue # 实时词元流 (Gloss) 与大模型译文双栏流式看板
│   │   │   ├── SignAvatar3D.vue     # Three.js 3D 手语数字人反向播报组件
│   │   │   └── SignPractice.vue     # AI 手语互动教学与动作相似度实时评分组件
│   │   ├── utils/
│   │   │   ├── landmarkNorm.ts      # 75点提取与人体空间归一化前端加速脚本
│   │   │   └── vadDetector.ts       # 前端运动能量检测与 WebSocket 分片发送
├── backend/                         # 后端流式推理与华为云集成服务 (Python + FastAPI)
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口与 WebSocket 实时双向通信路由
│   │   ├── engine/
│   │   │   ├── mindspore_infer.py   # 昇思 MindSpore / MindSpore Lite 实时手语模型推理封装
│   │   │   └── sliding_window.py    # 序列滑窗、CTC 解码与去重平滑逻辑
│   │   ├── huawei_cloud/
│   │   │   ├── maas_llm_client.py   # 华为云盘古/MaaS 大模型 Gloss-to-Text 流式翻译客户端
│   │   │   ├── sis_voice_client.py  # 华为云 SIS 语音合成 (TTS) 与语音识别 (ASR) 接口
│   │   │   └── obs_sync.py          # 华为云 OBS 模型拉取与困难样本自动回流脚本
├── mindspore_training/              # 昇思 MindSpore 模型训练工程 (可直接在 ModelArts 运行)
│   ├── dataset/
│   │   ├── collect_custom_csl.py    # 调用本地摄像头快速录制自定义比赛演示词库脚本
│   │   └── csl_skeleton_dataset.py  # MindSpore GeneratorDataset 数据加载与空间随机增强
│   ├── models/
│   │   ├── stgcn_transformer.py     # 基于 mindspore.nn 的双流 ST-GCN + Transformer 网络定义
│   │   └── graph_topology.py        # 75 关键点人体+双手自然骨骼邻接矩阵定义
│   ├── train_modelarts.py           # 适配华为云 ModelArts + 昇腾 910 的训练与评估主脚本
│   └── export_mindir.py             # 导出 .mindir 模型用于端/云高效部署
└── docs/                            # 华为 ICT 大赛创新赛交付物文档
    ├── modelarts_training_logs/     # ModelArts 训练截图与 Loss 收敛曲线
    └── architecture_slides.md       # 答辩 PPT 核心架构与亮点提纲
```

---

## 六、 四周敏捷开发与备赛冲刺路线图

### 第一阶段：跑通摄像头实时捕获与自建核心演示词库（第 1 周）
1. 搭建 `FastAPI + Vue3` 基础前后端，集成 MediaPipe Holistic 实现浏览器/本地摄像头 30 FPS 关键点提取与骨骼可视化渲染。
2. 编写 `collect_custom_csl.py` 采集脚本，挑选 **50 个高频核心手语词汇**（覆盖：**政务办理、医院问诊、日常高频交流**三大比赛演示黄金场景），团队成员每人录制 20~30 遍，结合公开 CSL 数据集构建高质量训练集。
   > **实战经验**：自己录制的样本与公开数据集混合训练，能保证比赛现场演示时识别准确率达到 **95% 以上**，绝对不会在评委面前翻车！

### 第二阶段：昇思 MindSpore 模型构建与华为云 ModelArts 训练（第 2 周）
1. 使用 `mindspore.nn` 实现双流 `ST-GCN + Transformer` 模型。
2. 将数据集上传至 **华为云 OBS**，在 **华为云 ModelArts** 上选择 `Ascend 910` 规格启动训练作业。
3. **重点留痕**：完整保存 ModelArts 控制台作业配置截图、昇腾 NPU 利用率监控图、Accuracy/Loss 曲线以及导出的 `.mindir` 模型文件（写入参赛技术报告必加分）。

### 第三阶段：接入华为云 MaaS 大模型与 SIS 语音服务（第 3 周）
1. 编写后端推理流水线，将摄像头实时识别出的 Gloss 序列接入 **华为云 ModelArts Studio (MaaS)** 接口，调优场景化 Prompt，实现低于 `500 ms` 的首字流式翻译输出。
2. 接入 **华为云语音交互服务（SIS）**，实现译文自动语音播报（TTS）以及健听人语音输入转文字（ASR）。

### 第四阶段：双向数字人闭环、手语教学模块与答辩打磨（第 4 周）
1. 在前端集成 Three.js 3D 虚拟人，绑定常用手语词根的骨骼关键帧动画，打通“健听人说话 $\rightarrow$ 3D 数字人打手语”反向链路。
2. 上线“AI 手语跟练评分”功能页与华为云监控大屏（实时显示推理帧率 FPS、端到端时延、昇思模型置信度曲线），完成最终路演 Demo 打包。
