import { AdminHeader } from "../components/AdminHeader";
import { Card } from "@/components/ui";
import { ThemePicker } from "@/components/theme";

/** 外观设置（桌面端）：与手机端共用 ThemePicker */
export default function AdminThemePage() {
  return (
    <div>
      <AdminHeader title="外观" desc="选择主色调方案，双端同步生效（保存在本设备）" />
      <ThemePicker />
      <Card className="mt-4 p-4">
        <p className="text-xs leading-relaxed text-ink-faint">
          主色调用于按钮、选中态、进度条与背景光晕。深色模式将在后续版本提供。
        </p>
      </Card>
    </div>
  );
}
