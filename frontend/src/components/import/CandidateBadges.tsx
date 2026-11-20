import { Badge } from "@/components/ui";
import type { ImportCandidate } from "@/data/types";
import { CANDIDATE_STATUS_META, classifyCandidateConfidence } from "@/domain/imports";
import { isConfirmable, isDuplicate, needsFxReview } from "@/lib/fx-review";

/** 仅对中/低置信度或数值型 OCR 置信度展示显著标签，避免常规高置信 CSV 行产生视觉噪音 */
export function CandidateConfidenceBadge({ confidence }: { confidence: string | null | undefined }) {
  const meta = classifyCandidateConfidence(confidence);
  if (!meta) return null;
  // 纯字符串 "high" 为普通表格解析默认态，不额外打标；数值百分比（OCR）与中/低置信度如实展示
  const isNumeric = confidence != null && /^\d+(?:\.\d+)?$/.test(confidence.trim());
  if (meta.level === "high" && !isNumeric) return null;
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

/** 候选状态徽标组（桌面与手机端共用同一判定口径） */
export function CandidateStatusBadges({
  candidate,
  showRowStatus = true,
  isConflictHighlighted = false,
}: {
  candidate: ImportCandidate;
  showRowStatus?: boolean;
  isConflictHighlighted?: boolean;
}) {
  const rowMeta = CANDIDATE_STATUS_META[candidate.status];
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {showRowStatus && <Badge tone={rowMeta.tone}>{rowMeta.label}</Badge>}
      {candidate.status === "pending" && (
        <CandidateConfidenceBadge confidence={candidate.confidence} />
      )}
      {needsFxReview(candidate) && <Badge tone="warn">待补汇率</Badge>}
      {candidate.status === "pending" &&
        !isConfirmable(candidate) &&
        !needsFxReview(candidate) &&
        !isDuplicate(candidate) && <Badge tone="warn">{candidate.validationStatus}</Badge>}
      {candidate.status === "pending" && !candidate.targetAccountId && (
        <Badge tone="warn">待选账户</Badge>
      )}
      {isConflictHighlighted && <Badge tone="danger">冲突待复核</Badge>}
    </span>
  );
}
