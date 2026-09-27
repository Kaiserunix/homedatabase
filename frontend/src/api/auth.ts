/**
 * 浏览器侧认证流程：密码兜底 + Passkey 优先。
 *
 * @simplewebauthn/browser 一律动态 import：登录页之外不加载，
 * 避免撑破每入口 90 kB 的 gzip 预算（scripts/check-budget.mjs）。
 * WebAuthn 的编解码全部交给该库，绝不手写字节处理。
 */
import type {
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";
import {
  login, passkeyAuthenticationOptions, passkeyAuthenticationVerify,
  passkeyRegistrationOptions, passkeyRegistrationVerify,
} from "./resources";
import type { AuthSession, Passkey, WebAuthnCeremony } from "@/data/types";

/** 服务端 options 是透传的 JSON，断言为 simplewebauthn 的选项类型 */
const asRequestOptions = (c: WebAuthnCeremony) =>
  c.options as unknown as PublicKeyCredentialRequestOptionsJSON;
const asCreationOptions = (c: WebAuthnCeremony) =>
  c.options as unknown as PublicKeyCredentialCreationOptionsJSON;

/** 当前环境是否支持 WebAuthn（需 HTTPS 或 localhost） */
export function isWebAuthnAvailable(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential !== "undefined";
}

/** 把 WebAuthn 常见异常翻译成人话 */
function toFriendlyError(err: unknown): Error {
  if (err instanceof DOMException && err.name === "NotAllowedError") {
    // 用户取消或系统拒绝（超时、无可用验证器等）
    return new Error("已取消或验证超时，请重试");
  }
  if (err instanceof DOMException && err.name === "SecurityError") {
    return new Error("当前环境不安全：Passkey 需要 HTTPS 或 localhost 环境");
  }
  return err instanceof Error ? err : new Error("Passkey 操作失败，请重试");
}

/** 密码登录（Passkey 之外的兜底方式） */
export async function loginWithPassword(username: string, password: string): Promise<AuthSession> {
  return login(username, password);
}

/** Passkey 登录：取认证选项 → 唤起系统验证器 → 服务端校验 */
export async function loginWithPasskey(username: string): Promise<AuthSession> {
  if (!isWebAuthnAvailable()) {
    throw new Error("当前浏览器或环境不支持 Passkey：需要 HTTPS 或 localhost，并使用支持 Passkey 的浏览器");
  }
  const ceremony = await passkeyAuthenticationOptions(username);
  try {
    const { startAuthentication } = await import("@simplewebauthn/browser");
    const credential = await startAuthentication({
      optionsJSON: asRequestOptions(ceremony),
    });
    return await passkeyAuthenticationVerify(ceremony.ceremonyId, credential);
  } catch (err) {
    throw toFriendlyError(err);
  }
}

/** 注册新 Passkey（需已登录会话）：取注册选项 → 唤起系统验证器 → 服务端校验 */
export async function registerPasskey(name: string): Promise<Passkey> {
  if (!isWebAuthnAvailable()) {
    throw new Error("当前浏览器或环境不支持 Passkey：需要 HTTPS 或 localhost，并使用支持 Passkey 的浏览器");
  }
  const ceremony = await passkeyRegistrationOptions();
  try {
    const { startRegistration } = await import("@simplewebauthn/browser");
    const credential = await startRegistration({
      optionsJSON: asCreationOptions(ceremony),
    });
    return await passkeyRegistrationVerify(ceremony.ceremonyId, name, credential);
  } catch (err) {
    throw toFriendlyError(err);
  }
}
