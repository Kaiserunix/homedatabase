import { Link, useNavigate } from "react-router-dom";
import { useState, type CSSProperties } from "react";
import {
  ArrowLeftRight, ChevronRight, FileUp, KeyRound, Landmark, LogOut,
  MonitorSmartphone, Palette, Users, type LucideIcon,
} from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { Badge, Card, MemberAvatar } from "@/components/ui";
import { useAuth } from "@/auth/AuthContext";

type Entry = { icon: LucideIcon; label: string; desc?: string } & (
  | { to: string; href?: never; action?: never }
  | { href: string; to?: never; action?: never }
  | { action: () => void; to?: never; href?: never }
);

function Row({ item, last, style }: { item: Entry; last: boolean; style?: CSSProperties }) {
  const Icon = item.icon;
  const inner = (
    <>
      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-900/5 text-ink-soft">
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-ink">{item.label}</span>
        {item.desc && <span className="block text-xs text-ink-faint">{item.desc}</span>}
      </span>
      <ChevronRight className="h-4 w-4 shrink-0 text-ink-faint" />
    </>
  );
  const cls = `flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-900/[0.03] ${
    last ? "" : "border-b border-slate-900/5"
  }`;
  if (item.action) {
    return (
      <button type="button" onClick={item.action} className={cls} style={style}>
        {inner}
      </button>
    );
  }
  return item.to ? (
    <Link to={item.to} className={cls} style={style}>
      {inner}
    </Link>
  ) : (
    <a href={item.href} target="_blank" rel="noreferrer" className={cls} style={style}>
      {inner}
    </a>
  );
}

export default function MePage() {
  const { member, signOut } = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  const onSignOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await signOut();
      navigate("/login", { replace: true });
    } finally {
      setSigningOut(false);
    }
  };

  const groups: { title: string; items: Entry[] }[] = [
    {
      title: "记账",
      items: [
        { icon: Landmark, label: "账户与资产负债", to: "/me/accounts" },
        { icon: FileUp, label: "导入账单", to: "/me/import" },
      ],
    },
    {
      title: "家庭",
      items: [
        { icon: Users, label: "家庭公共账户", to: "/me/accounts" },
        { icon: ArrowLeftRight, label: "分摊", to: "/splits" },
      ],
    },
    {
      title: "安全",
      items: [{ icon: KeyRound, label: "Passkey 与登录方式", to: "/me/security" }],
    },
    {
      title: "设置",
      items: [{ icon: Palette, label: "外观与颜色", desc: "主题色方案", to: "/me/theme" }],
    },
    // 桌面管理台入口仅管理员可见（后端同样会做权限校验，这里不展示入口）
    ...(member?.isAdmin
      ? [
          {
            title: "高级",
            items: [{ icon: MonitorSmartphone, label: "桌面管理台", desc: "新标签页打开", href: "/admin.html" }],
          },
        ]
      : []),
    {
      title: "账号",
      items: [
        {
          icon: LogOut,
          label: signingOut ? "正在退出…" : "退出登录",
          desc: member ? `当前账号：${member.username}` : undefined,
          action: () => void onSignOut(),
        },
      ],
    },
  ];

  return (
    <div className="px-4">
      <PageHeader title="我的" />

      <Card className="flex items-center gap-3 p-4">
        <MemberAvatar name={member?.name ?? "?"} color={member?.color ?? "bg-slate-400"} size="md" />
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold text-ink">{member?.name ?? "—"}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {member?.isAdmin ? <Badge tone="accent">管理员</Badge> : <Badge tone="neutral">成员</Badge>}
          </div>
        </div>
      </Card>

      {groups.map((g) => (
        <section key={g.title} className="mt-5">
          <h2 className="mb-2 px-1 text-xs font-medium text-ink-faint">{g.title}</h2>
          <Card className="stagger overflow-hidden">
            {g.items.map((item, i) => (
              <Row
                key={item.label}
                item={item}
                last={i === g.items.length - 1}
                style={{ "--stagger-i": i } as CSSProperties}
              />
            ))}
          </Card>
        </section>
      ))}

      <p className="mt-8 text-center text-[11px] text-ink-faint">
        账目数据加密备份，实际存放位置以管理台「备份与恢复」页显示为准
      </p>
    </div>
  );
}
