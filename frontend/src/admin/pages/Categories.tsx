import { useEffect, useState } from "react";
import { Ban, CheckCircle2, Pencil, Plus, Shapes } from "lucide-react";
import { AdminHeader } from "../components/AdminHeader";
import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { dataIcon, DATA_ICON_NAMES } from "@/components/icons";
import { useApiQuery } from "@/api/useApi";
import { createCategory, listCategories, updateCategory } from "@/api/resources";
import { ApiError } from "@/api/client";
import type { Category, CategoryKind } from "@/data/types";

const kindMeta: Record<CategoryKind, { label: string; tone: "neutral" | "accent" }> = {
  expense: { label: "支出", tone: "neutral" },
  income: { label: "收入", tone: "accent" },
};

const inputCls = "glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint";

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/** 409 版本冲突提示：编辑框已关闭、列表已刷新，必须重新打开核对后再保存 */
const CONFLICT_COPY = "该分类刚被其他人修改，已为你加载最新列表；如仍需修改，请重新打开编辑框核对后再保存。";

/** 表格骨架：与下方分类表格行位大致对齐 */
function CategoryTableSkeleton() {
  return (
    <div className="space-y-3 px-4 py-3" aria-busy="true" aria-label="加载中">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
          <Skeleton className="h-3.5 w-32 shrink-0" />
          <Skeleton className="h-5 w-12 shrink-0 rounded-full" />
          <div className="flex-1" />
          <Skeleton className="h-8 w-14 shrink-0 rounded-xl" />
          <Skeleton className="h-8 w-14 shrink-0 rounded-xl" />
        </div>
      ))}
    </div>
  );
}

interface CategoryFormState {
  name: string;
  kind: CategoryKind;
  icon: string;
  /** "none" = 一级分类 */
  parentId: string;
}

export default function CategoriesPage() {
  // 管理页需要看到（并重新启用）已停用分类：includeInactive=true（仅管理员可用）
  const { data, loading, error, retry } = useApiQuery(() => listCategories({ includeInactive: true }), []);
  // 写操作成功后以响应为准更新本地副本（版本号随之更新），列表刷新后整体重置
  const [items, setItems] = useState<Category[]>([]);
  useEffect(() => {
    if (data) setItems(data);
  }, [data]);

  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState<CategoryFormState>({ name: "", kind: "expense", icon: "shapes", parentId: "none" });
  const [editTarget, setEditTarget] = useState<Category | null>(null);
  const [editForm, setEditForm] = useState({ name: "", icon: "shapes" });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // 409 写锁：true 期间禁止一切分类写操作（新增/编辑/停用），直到刷新最新列表成功
  const [writeLocked, setWriteLocked] = useState(false);

  const roots = (kind: CategoryKind) => items.filter((c) => c.kind === kind && c.parentId === null);
  const childrenOf = (id: string) => items.filter((c) => c.parentId === id);
  // 父分类候选只取启用中的一级分类：停用的分类不能再挂新子分类
  const activeRoots = (kind: CategoryKind) => roots(kind).filter((c) => c.active);

  const replaceItem = (next: Category) =>
    setItems((list) => list.map((c) => (c.id === next.id ? next : c)));

  /**
   * 409 冲突：同步废弃编辑框/新增框与旧表单、进入写锁，再 try 拉取最新列表。
   * 仅 GET 成功替换完整列表后才解锁并提示「已刷新」；GET 失败（含 apiFetch
   * 自动重试后仍失败）诚实提示刷新失败、维持写锁，由「重试刷新」重走本函数。
   * 本函数捕获一切异常、绝不向上抛（事件以 void 启动，避免未处理 rejection）。
   */
  const refreshAfterConflict = async () => {
    setEditTarget(null);
    setAddOpen(false);
    // 同步重置新增表单：旧 name/kind/parentId（父分类可能已停用）不得带入重开后的提交
    setAddForm({ name: "", kind: "expense", icon: "shapes", parentId: "none" });
    setFormError(null);
    setWriteLocked(true);
    try {
      setItems(await listCategories({ includeInactive: true }));
      setWriteLocked(false);
      setActionError(CONFLICT_COPY);
    } catch {
      setActionError("该分类刚被其他人修改，且刷新最新列表失败。请点击「重试刷新」，成功后再继续操作。");
    }
  };

  /** 非 409 写错误：保留页面状态并展示真实错误 */
  const handleWriteError = (e: unknown, setLocal?: (msg: string | null) => void) => {
    (setLocal ?? setActionError)(errMsg(e));
  };

  const doCreate = async () => {
    if (writeLocked || busy) return;
    if (!addForm.name.trim()) {
      setFormError("请填写分类名称");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const created = await createCategory({
        name: addForm.name.trim(),
        kind: addForm.kind,
        icon: addForm.icon,
        parentId: addForm.parentId === "none" ? null : addForm.parentId,
      });
      setItems((list) => [...list, created]);
      setAddOpen(false);
      setAddForm({ name: "", kind: addForm.kind, icon: "shapes", parentId: "none" });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        handleWriteError(e, setFormError);
      }
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    if (!editTarget || writeLocked || busy) return;
    if (!editForm.name.trim()) {
      setFormError("请填写分类名称");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      replaceItem(
        await updateCategory(editTarget.id, {
          name: editForm.name.trim(),
          icon: editForm.icon,
          expectedVersion: editTarget.version,
        }),
      );
      setEditTarget(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // refreshAfterConflict 内部同步废弃编辑框与旧表单、按刷新成败解锁/锁定，
        // 自身不抛异常——这里 await 后无需再做任何状态处理
        await refreshAfterConflict();
      } else {
        setFormError(errMsg(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (c: Category) => {
    if (busy || writeLocked) return;
    setBusy(true);
    setActionError(null);
    try {
      replaceItem(await updateCategory(c.id, { active: !c.active, expectedVersion: c.version }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        setActionError(errMsg(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const renderRow = (c: Category, depth: 0 | 1) => {
    const Icon = dataIcon(c.icon);
    return (
      <tr key={c.id} className="border-t border-slate-900/5">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2.5" style={depth === 1 ? { paddingLeft: "1.5rem" } : undefined}>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-900/5 text-ink-soft">
              <Icon className="h-4 w-4" />
            </span>
            <span className={c.active ? "text-ink" : "text-ink-faint line-through"}>{c.name}</span>
            {depth === 1 && <span className="text-[11px] text-ink-faint">子分类</span>}
          </div>
        </td>
        <td className="px-4 py-2.5">
          <Badge tone={kindMeta[c.kind].tone}>{kindMeta[c.kind].label}</Badge>
        </td>
        <td className="px-4 py-2.5">
          {c.active ? <Badge tone="ok">启用中</Badge> : <Badge tone="warn">已停用</Badge>}
        </td>
        <td className="px-4 py-2.5 text-xs text-ink-faint">v{c.version}</td>
        <td className="px-4 py-2.5 text-right">
          <div className="flex justify-end gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              disabled={busy || writeLocked}
              onClick={() => {
                setEditTarget(c);
                setEditForm({ name: c.name, icon: c.icon });
                setFormError(null);
              }}
            >
              <Pencil className="h-3.5 w-3.5" /> 编辑
            </Button>
            <Button
              variant={c.active ? "danger" : "secondary"}
              size="sm"
              disabled={busy || writeLocked}
              onClick={() => void toggleActive(c)}
            >
              {c.active ? <Ban className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              {c.active ? "停用" : "启用"}
            </Button>
          </div>
        </td>
      </tr>
    );
  };

  return (
    <div>
      <AdminHeader
        title="分类管理"
        desc="两级自定义分类：一级分类下可建子分类；停用后不再出现在记账与导入的分类选项中"
        action={
          <Button
            disabled={writeLocked}
            onClick={() => {
              setAddOpen(true);
              setFormError(null);
            }}
          >
            <Plus className="h-4 w-4" /> 新增分类
          </Button>
        }
      />

      {actionError && (
        <p role="alert" className="mb-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {actionError}
        </p>
      )}
      {writeLocked && (
        <div className="mb-4">
          <Button variant="secondary" size="sm" onClick={() => void refreshAfterConflict()}>
            重试刷新
          </Button>
        </div>
      )}

      <Card className="overflow-hidden">
        {loading ? (
          <CategoryTableSkeleton />
        ) : error ? (
          <ErrorState title="分类加载失败" desc={error.message} onRetry={retry} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Shapes className="h-8 w-8" />}
            title="还没有分类"
            hint="点击右上角「新增分类」创建第一个分类"
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-ink-faint">
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">方向</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">版本</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {(["expense", "income"] as const).map((kind) =>
                roots(kind).flatMap((root) => [
                  renderRow(root, 0),
                  ...childrenOf(root.id).map((child) => renderRow(child, 1)),
                ]),
              )}
            </tbody>
          </table>
        )}
      </Card>

      {/* 新增分类 */}
      <Dialog open={addOpen} onOpenChange={(o) => { setAddOpen(o); if (o) setFormError(null); }}>
        <DialogContent>
          <DialogHeader title="新增分类" desc="最多两级：不选父分类即创建一级分类" />
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">名称（必填）</span>
              <input
                value={addForm.name}
                onChange={(e) => {
                  setAddForm((f) => ({ ...f, name: e.target.value }));
                  setFormError(null);
                }}
                placeholder="例如：餐饮"
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">方向</span>
              <Select
                value={addForm.kind}
                onValueChange={(v) =>
                  setAddForm((f) => ({ ...f, kind: v as CategoryKind, parentId: "none" }))
                }
              >
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value="expense">支出</SelectItem>
                  <SelectItem value="income">收入</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">父分类（选填，仅同方向的启用中一级分类）</span>
              <Select
                value={addForm.parentId}
                onValueChange={(v) => setAddForm((f) => ({ ...f, parentId: v }))}
              >
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value="none">无（作为一级分类）</SelectItem>
                  {activeRoots(addForm.kind).map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">图标</span>
              <Select
                value={addForm.icon}
                onValueChange={(v) => setAddForm((f) => ({ ...f, icon: v }))}
              >
                <SelectTrigger />
                <SelectContent>
                  {DATA_ICON_NAMES.map((name) => (
                    <SelectItem key={name} value={name}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {formError && <p role="alert" className="text-xs text-danger">{formError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => setAddOpen(false)}>取消</Button>
              <Button disabled={busy || writeLocked} onClick={() => void doCreate()}>{busy ? "保存中…" : "保存"}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 编辑分类（改名 / 换图标，携带 expectedVersion） */}
      <Dialog open={editTarget !== null} onOpenChange={(o) => !o && setEditTarget(null)}>
        <DialogContent>
          <DialogHeader title="编辑分类" desc="修改名称与图标；版本号防并发覆盖" />
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">名称（必填）</span>
              <input
                value={editForm.name}
                onChange={(e) => {
                  setEditForm((f) => ({ ...f, name: e.target.value }));
                  setFormError(null);
                }}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">图标</span>
              <Select
                value={editForm.icon}
                onValueChange={(v) => setEditForm((f) => ({ ...f, icon: v }))}
              >
                <SelectTrigger />
                <SelectContent>
                  {DATA_ICON_NAMES.map((name) => (
                    <SelectItem key={name} value={name}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {editTarget && (
              <p className="text-xs text-ink-faint">当前版本 v{editTarget.version}，保存时携带版本号防并发覆盖。</p>
            )}
            {formError && <p role="alert" className="text-xs text-danger">{formError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => setEditTarget(null)}>取消</Button>
              <Button disabled={busy || writeLocked} onClick={() => void doRename()}>{busy ? "保存中…" : "保存"}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
