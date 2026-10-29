// AudioCpp 节点按钮扩展:
//   - 所有 AudioCpp 节点追加「🚫 取消任务」按钮(触发 ComfyUI 中断当前工作流);
//   - 「AudioCpp 卸载模型(释放显存)」节点额外追加「🗑️ 卸载显存」按钮
//     (弹 confirm 确认后,经后端代理路由 /audionode/unload_models 直接卸载,
//      无需整图执行;结果回显到节点状态行)。
// 参考 ArcReel selector.js 与 jon_local_llm_loader_native.js 的写法:
// node.addWidget("button", ...) + serialize:false(不随工作流持久化)+
// 幂等 addIfAbsent + node.setSize(node.computeSize())。
//
// 关键适配(本环境前端包 1.57.x / ComfyUI 0.42):
//   1) ComfyUI 前端以 ES module 方式加载扩展 js,必须用 import 拿 app,
//      不能依赖全局 window.app。
//   2) 新版 LGraphNode.addWidget 签名为 (type, name, value, callback, options):
//      canvas 上按钮文字取的是 name,而非第三参数;故把显示文本直接作为
//      name 传入,第三参数(value)也传同一文本。
//   3) 补 widget.label / getLabel 字段,避免 GraphView 的 text-content
//      读到 undefined.label 而崩溃。
//   4) nodeCreated 只对新创建节点触发;工作流加载(旧节点)走 onConfigure,
//      统一调用 ensureButtons 保证新旧节点都挂上按钮(幂等)。
import { app } from "../../../../scripts/app.js";

const AUDIO_CPP_NODE_CLASSES = [
    "AudioCppTextToSpeech",
    "AudioCppSpeechToText",
    "AudioCppAudioTask",
    "AudioCppUnloadModels",
    "AudioCppServerInfo",
];

const UNLOAD_NODE_CLASS = "AudioCppUnloadModels";

// 显示文本直接作为 widget name(新版 canvas 按钮文字取 name)。
const CANCEL_BTN_LABEL = "🚫 取消任务";
const UNLOAD_BTN_LABEL = "🗑️ 卸载显存";
const STATUS_WIDGET_NAME = "状态";

function isAudioCppNode(node) {
    return AUDIO_CPP_NODE_CLASSES.includes(node.comfyClass);
}

// 新版 addWidget 的第三参数是 value;传入显示文本后补 label/getLabel,
// 防止 GraphView 的 text-content 读 undefined.label 崩溃。
function addWidgetIfAbsent(node, type, name, label, callback, options) {
    const existing = (node.widgets || []).find((w) => w.name === name);
    if (existing) return existing;
    const w = node.addWidget(type, name, label, callback, options);
    w.label = label;
    if (typeof w.getLabel !== "function") w.getLabel = () => label;
    return w;
}

function setStatus(node, text) {
    let w = (node.widgets || []).find((x) => x.name === STATUS_WIDGET_NAME);
    if (!w) {
        w = addWidgetIfAbsent(
            node, "string", STATUS_WIDGET_NAME, STATUS_WIDGET_NAME, () => {},
            { serialize: false },
        );
    }
    w.value = text;
    node.setDirtyCanvas?.(true, true);
}

async function cancelWorkflow(node) {
    // ComfyUI 后端 POST /interrupt:中断当前正在运行的工作流
    // (与工具栏「停止」按钮同一机制)。
    setStatus(node, "⏳ 正在取消任务…");
    try {
        const resp = await fetch("/interrupt", { method: "POST" });
        if (resp.ok) {
            setStatus(node, "✅ 已发送取消(正在运行的节点将中断)");
        } else {
            setStatus(node, `❌ 取消失败:HTTP ${resp.status}`);
        }
    } catch (e) {
        setStatus(node, `❌ 取消失败:${e}`);
    }
}

async function unloadVram(node) {
    // 读取节点当前 widget 值,经后端代理卸载(浏览器不能直连 audio.cpp:无 CORS)
    const value = (name) => {
        const w = (node.widgets || []).find((x) => x.name === name);
        return w ? w.value : undefined;
    };
    const body = {
        host: value("host") || "",
        port: value("port") || 0,
        mode: value("mode") || "unload_all",
        model_ids: value("model_ids") || "",
    };

    const btn = (node.widgets || []).find((w) => w.name === UNLOAD_BTN_LABEL);
    // 按钮 widget 的 value 即显示文本;临时改为「卸载中」
    if (btn) btn.value = "⏳ 卸载中…";
    setStatus(node, "⏳ 正在卸载模型(释放显存)…");

    try {
        const resp = await fetch("/audionode/unload_models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.ok) {
            setStatus(node, `✅ ${data.report || "卸载完成"}`);
        } else {
            setStatus(node, `❌ ${data.error || `HTTP ${resp.status}`}`);
        }
    } catch (e) {
        setStatus(node, `❌ 卸载请求失败:${e}`);
    } finally {
        if (btn) btn.value = UNLOAD_BTN_LABEL;
    }
}

// 幂等地为单个节点挂按钮(与 jon_local_llm_loader 的 addIfAbsent 同一模式)。
// 按钮 serialize:false 从不随工作流持久化,故新节点始终由 nodeCreated 重建,
// 不存在"旧按钮名"迁移问题。
function ensureButtons(node) {
    if (!isAudioCppNode(node)) return;
    if (typeof node.addWidget !== "function") return;
    if (node.__audionode_buttons) return;

    // 所有 AudioCpp 节点:取消任务按钮
    addWidgetIfAbsent(
        node, "button", CANCEL_BTN_LABEL, CANCEL_BTN_LABEL,
        () => { cancelWorkflow(node); },
        { serialize: false },
    );

    // 仅卸载节点:卸载显存按钮(带 confirm)
    if (node.comfyClass === UNLOAD_NODE_CLASS) {
        addWidgetIfAbsent(
            node, "button", UNLOAD_BTN_LABEL, UNLOAD_BTN_LABEL,
            () => {
                const ok = window.confirm(
                    "将直接卸载 audio.cpp 服务器上已加载的模型,释放显存/内存。\n" +
                    "卸载后模型需重新加载才能使用。确定继续?",
                );
                if (ok) unloadVram(node);
            },
            { serialize: false },
        );
    }

    node.__audionode_buttons = true;
    node.setSize?.(node.computeSize?.());
}

app.registerExtension({
    name: "AudioCpp.Buttons",
    getExtraClass: (node) => (isAudioCppNode(node) ? "audiocpp-buttons-node" : undefined),
    // 本版本 registerExtension 仅保留 nodeCreated 等白名单钩子(graph 级
    // onGraphConfigured 不被 dispatch);按钮 serialize:false 不落盘,
    // 新节点统一由 nodeCreated 重建,故无需 graph 级钩子(与 jon 一致)。
    nodeCreated: (node) => { ensureButtons(node); },
});
