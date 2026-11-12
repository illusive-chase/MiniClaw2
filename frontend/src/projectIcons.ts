/* Project icon vocabulary: the render side (design §3).
 *
 * Only our own glyph keys are stored, never lucide component names, so a library
 * swap leaves saved data untouched. The backend (`project_icons.py`) holds its
 * own copy of both key lists for validation — two copies is intentional, so
 * Python never has to read frontend assets.
 */
import {
  Activity, Anchor, Atom, BookOpen, Bot, Boxes, Braces, Brain, Bug, Building,
  Calendar, Camera, ChartLine, ChartPie, Cloud, Code, Compass, Cpu, Database,
  Dna, Feather, FileText, Flame, FlaskConical, Gamepad2, GitBranch, Globe,
  GraduationCap, Hammer, HardDrive, Heart, House, ImageIcon, KeyRound, Layers,
  Leaf, Lightbulb, Lock, Mail, MapIcon, MessageCircle, Microscope, Monitor,
  Moon, Music, Network, Package, Palette, PenTool, Plane, Puzzle, Rocket,
  Server, Shield, ShoppingCart, Sigma, Smartphone, Sparkles, Star, Sun, Target,
  Terminal, Trophy, Users, Video, Wallet, Workflow, Wrench, Zap,
  type LucideIcon,
} from "lucide-react";

export type GlyphDef = {
  key: string;
  icon: LucideIcon;
  /** Search keywords; the first word doubles as the display name. */
  keywords: string;
};

export type GlyphCategory = {
  id: string;
  label: string;
  glyphs: GlyphDef[];
};

const g = (key: string, icon: LucideIcon, keywords: string): GlyphDef => ({ key, icon, keywords });

export const GLYPH_CATEGORIES: GlyphCategory[] = [
  {
    id: "dev",
    label: "开发",
    glyphs: [
      g("code", Code, "代码 code"),
      g("terminal", Terminal, "终端 命令行 cli"),
      g("braces", Braces, "json 配置"),
      g("git-branch", GitBranch, "git 分支"),
      g("bug", Bug, "调试 bug"),
      g("package", Package, "包 库 npm"),
      g("boxes", Boxes, "模块 容器"),
      g("layers", Layers, "层 架构"),
      g("puzzle", Puzzle, "插件 扩展"),
      g("workflow", Workflow, "流程 编排"),
      g("wrench", Wrench, "工具 修复"),
      g("hammer", Hammer, "构建 build"),
      g("cpu", Cpu, "芯片 硬件"),
      g("bot", Bot, "机器人 agent"),
      g("sparkles", Sparkles, "ai 生成"),
      g("rocket", Rocket, "发布 启动"),
    ],
  },
  {
    id: "infra",
    label: "基础设施与数据",
    glyphs: [
      g("database", Database, "数据库 db"),
      g("server", Server, "服务器 后端"),
      g("cloud", Cloud, "云 部署"),
      g("network", Network, "网络"),
      g("globe", Globe, "网站 web"),
      g("shield", Shield, "安全"),
      g("lock", Lock, "锁 权限"),
      g("key-round", KeyRound, "密钥 认证"),
      g("hard-drive", HardDrive, "存储 磁盘"),
      g("activity", Activity, "监控 指标"),
      g("chart-line", ChartLine, "图表 趋势"),
      g("chart-pie", ChartPie, "统计 分析"),
    ],
  },
  {
    id: "research",
    label: "研究与学习",
    glyphs: [
      g("brain", Brain, "模型 思考"),
      g("flask", FlaskConical, "实验"),
      g("microscope", Microscope, "研究"),
      g("atom", Atom, "物理 科学"),
      g("dna", Dna, "生物"),
      g("sigma", Sigma, "数学 公式"),
      g("book-open", BookOpen, "文档 阅读"),
      g("graduation-cap", GraduationCap, "学习 课程"),
      g("lightbulb", Lightbulb, "想法 灵感"),
      g("target", Target, "目标"),
    ],
  },
  {
    id: "content",
    label: "内容与沟通",
    glyphs: [
      g("file-text", FileText, "文稿 写作"),
      g("pen-tool", PenTool, "设计 矢量"),
      g("palette", Palette, "配色 美术"),
      g("image", ImageIcon, "图片"),
      g("music", Music, "音乐 音频"),
      g("video", Video, "视频"),
      g("camera", Camera, "摄影"),
      g("feather", Feather, "写作 博客"),
      g("message-circle", MessageCircle, "聊天 消息"),
      g("mail", Mail, "邮件"),
    ],
  },
  {
    id: "life",
    label: "生活与其他",
    glyphs: [
      g("house", House, "家 主页"),
      g("building", Building, "公司 组织"),
      g("map", MapIcon, "地图"),
      g("compass", Compass, "导航 探索"),
      g("plane", Plane, "旅行"),
      g("shopping-cart", ShoppingCart, "电商 购物"),
      g("wallet", Wallet, "钱包 财务"),
      g("gamepad", Gamepad2, "游戏"),
      g("trophy", Trophy, "奖杯 比赛"),
      g("heart", Heart, "喜欢 健康"),
      g("star", Star, "收藏 重要"),
      g("sun", Sun, "白天"),
      g("moon", Moon, "夜间"),
      g("leaf", Leaf, "自然 环保"),
      g("flame", Flame, "热门"),
      g("zap", Zap, "快速 性能"),
      g("anchor", Anchor, "锚 稳定"),
      g("smartphone", Smartphone, "移动 app"),
      g("monitor", Monitor, "桌面 屏幕"),
      g("calendar", Calendar, "日程"),
      g("users", Users, "团队 用户"),
    ],
  },
];

const GLYPHS = new Map<string, GlyphDef>(
  GLYPH_CATEGORIES.flatMap((category) => category.glyphs.map((glyph) => [glyph.key, glyph])),
);

/** Ordered — the swatch row follows it. */
export const PROJECT_HUES = [
  "coral", "amber", "sage", "teal", "azure", "indigo", "plum", "clay",
] as const;

export type ProjectHue = (typeof PROJECT_HUES)[number];

export const HUE_NAMES: Record<ProjectHue, string> = {
  coral: "珊瑚",
  amber: "琥珀",
  sage: "鼠尾草",
  teal: "青",
  azure: "天蓝",
  indigo: "靛蓝",
  plum: "梅紫",
  clay: "陶土",
};

/* Tailwind scans source statically, so every class has to appear as a literal. */
const TEXT_CLASS: Record<ProjectHue, string> = {
  coral: "text-hue-coral",
  amber: "text-hue-amber",
  sage: "text-hue-sage",
  teal: "text-hue-teal",
  azure: "text-hue-azure",
  indigo: "text-hue-indigo",
  plum: "text-hue-plum",
  clay: "text-hue-clay",
};

const SWATCH_CLASS: Record<ProjectHue, string> = {
  coral: "bg-hue-coral",
  amber: "bg-hue-amber",
  sage: "bg-hue-sage",
  teal: "bg-hue-teal",
  azure: "bg-hue-azure",
  indigo: "bg-hue-indigo",
  plum: "bg-hue-plum",
  clay: "bg-hue-clay",
};

const RING_CLASS: Record<ProjectHue, string> = {
  coral: "ring-hue-coral",
  amber: "ring-hue-amber",
  sage: "ring-hue-sage",
  teal: "ring-hue-teal",
  azure: "ring-hue-azure",
  indigo: "ring-hue-indigo",
  plum: "ring-hue-plum",
  clay: "ring-hue-clay",
};

/* Migration-written default and the fallback for a key this build lacks — a
 * record synced from a newer client renders, rather than throwing. */
export const FALLBACK_GLYPH = "code";
export const FALLBACK_HUE: ProjectHue = "coral";

export function isProjectHue(value: string): value is ProjectHue {
  return (PROJECT_HUES as readonly string[]).includes(value);
}

export function glyphDef(key: string): GlyphDef {
  return GLYPHS.get(key) ?? GLYPHS.get(FALLBACK_GLYPH)!;
}

export function isKnownGlyph(key: string): boolean {
  return GLYPHS.has(key);
}

export function glyphName(key: string): string {
  return glyphDef(key).keywords.split(" ")[0];
}

function hue(value: string): ProjectHue {
  return isProjectHue(value) ? value : FALLBACK_HUE;
}

export function hueTextClass(value: string): string {
  return TEXT_CLASS[hue(value)];
}

export function hueSwatchClass(value: string): string {
  return SWATCH_CLASS[hue(value)];
}

export function hueRingClass(value: string): string {
  return RING_CLASS[hue(value)];
}

/** Case-insensitive match on key or keywords; empty query keeps everything. */
export function searchGlyphs(query: string): GlyphCategory[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return GLYPH_CATEGORIES;
  return GLYPH_CATEGORIES
    .map((category) => ({
      ...category,
      glyphs: category.glyphs.filter(
        (glyph) => glyph.key.includes(needle) || glyph.keywords.toLowerCase().includes(needle),
      ),
    }))
    .filter((category) => category.glyphs.length > 0);
}
