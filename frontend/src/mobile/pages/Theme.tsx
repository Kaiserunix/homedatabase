import { PageHeader } from "../components/PageHeader";
import { Card } from "@/components/ui";
import { ThemePicker } from "@/components/theme";

/** 外观设置：主题色方案切换（本地持久化，双端同步生效） */
export default function ThemePage() {
  return (
    <div className="px-4">
      <PageHeader back title="外观与颜色" sub="选择主色调，立即生效" />
      <div className="stagger">
        <ThemePicker />
      </div>
      <Card className="mt-4 p-4">
        <p className="text-xs leading-relaxed text-ink-faint">
          主色调用于按钮、选中态、进度条与背景光晕。选择会保存在本设备，
          手机端与桌面管理台共用同一套方案。深色模式将在后续版本提供。
        </p>
      </Card>
    </div>
  );
}
