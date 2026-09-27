import {
  Baby, Banknote, Briefcase, CreditCard, DollarSign, Gamepad2, Gift,
  HeartPulse, Home, Landmark, Shapes, ShoppingCart, Sofa, TrainFront,
  TrendingUp, Users, Utensils, Wallet, HelpCircle, type LucideIcon,
} from "lucide-react";

/** 数据里的 icon 字符串 -> lucide 组件的统一映射 */
const map: Record<string, LucideIcon> = {
  utensils: Utensils,
  "shopping-cart": ShoppingCart,
  "train-front": TrainFront,
  sofa: Sofa,
  "heart-pulse": HeartPulse,
  "gamepad-2": Gamepad2,
  baby: Baby,
  shapes: Shapes,
  briefcase: Briefcase,
  gift: Gift,
  "trending-up": TrendingUp,
  landmark: Landmark,
  wallet: Wallet,
  banknote: Banknote,
  "dollar-sign": DollarSign,
  users: Users,
  "credit-card": CreditCard,
  home: Home,
};

export function dataIcon(name: string | null | undefined): LucideIcon {
  return (name && map[name]) || HelpCircle;
}

/** 可选图标名列表（分类管理等表单用） */
export const DATA_ICON_NAMES: string[] = Object.keys(map);
