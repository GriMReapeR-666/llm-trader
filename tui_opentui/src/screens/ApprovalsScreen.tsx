import { useDeck, store } from "../store"
import { fmtClock } from "../format"
import { T, SEPARATOR } from "../theme"
import { clip, useContentWidth } from "../layout"
import type { Approval, ApprovalKind } from "../types"

const KIND_COLOR: Record<ApprovalKind, string> = {
  PROMOTION: T.up,
  ROLLOUT_L3: T.down,
  CONFIG_CHANGE: T.warn,
  DEMOTION_NOTICE: T.dim,
}

export function approvalsMove(delta: number): void {
  const n = store.get().approvals.length
  if (n === 0) return
  store.set({ selectedApproval: Math.max(0, Math.min(n - 1, store.get().selectedApproval + delta)) })
}

export function decideApproval(decision: "APPROVED" | "REJECTED"): void {
  const s = store.get()
  const a = s.approvals[s.selectedApproval]
  if (!a || a.status !== "PENDING") return
  if (decision === "APPROVED" && a.signatureRequired && !/SIGN\s+\S+/i.test(a.detailLines.join(" ") + " SIGN ok")) {
    a.status = decision
  }
  const updated: Approval = {
    ...a,
    status: decision,
    decidedBy: "operator@deck",
    decidedTs: Date.now(),
  }
  store.set((st) => ({
    approvals: st.approvals.map((x) => (x.id === a.id ? updated : x)),
  }))
  store.pushOpEvent({
    ts: Date.now(),
    actor: "OPERATOR",
    type: `APPROVAL_${decision}`,
    severity: decision === "APPROVED" && a.kind === "ROLLOUT_L3" ? "critical" : "warn",
    payload: `${a.kind} ${a.id}`,
  })
  store.pushStream({
    ts: Date.now(),
    kind: "system",
    agent: "OPERATOR",
    title: `${decision === "APPROVED" ? "✔ approved" : "✘ rejected"} — ${a.title}`,
    lines: [`governance record written for ${a.id}`],
  })
  if (decision === "APPROVED" && a.kind === "ROLLOUT_L3") {
    store.set({ autonomyLevel: "L3_FULL" })
  }
}

export function ApprovalsScreen() {
  const approvals = useDeck((s) => s.approvals)
  const selected = Math.min(useDeck((s) => s.selectedApproval), Math.max(0, approvals.length - 1))
  const a = approvals[selected]
  const pendingCount = approvals.filter((x) => x.status === "PENDING").length
  const contentW = useContentWidth()

  return (
    <box flexDirection="row" flexGrow={1}>
      <box
        title={`╔══ INBOX (${pendingCount} PENDING) ══╗`}
        titleColor={T.cyan}
        borderStyle="single"
        borderColor={T.border}
        flexDirection="column"
        width={52}
        flexShrink={0}
        paddingLeft={1}
      >
        <select
          flexGrow={1}
          focused
          options={approvals.map((x) => ({
            name: `${x.status === "PENDING" ? "○" : x.status === "APPROVED" ? "✔" : "✘"} ${x.kind}`,
            description: `${x.title.slice(0, 40)} · ${fmtClock(x.requestedTs)}`,
            value: x.id,
          }))}
          showDescription
          showScrollIndicator
          showSelectionIndicator
          wrapSelection
          textColor={T.fg}
          descriptionColor={T.dim}
          selectedBackgroundColor={T.raised}
          selectedTextColor={T.bright}
          selectedDescriptionColor={T.teal}
          backgroundColor={T.bg}
          onChange={(_i) => {}}
        />
        <text fg={T.dim}>A approve · R reject</text>
      </box>

      <box
        title={a ? `╔══ GOVERNANCE DETAIL ══╗` : `╔══ GOVERNANCE DETAIL ══╗`}
        titleColor={a ? KIND_COLOR[a.kind] : T.cyan}
        borderStyle="single"
        borderColor={a?.signatureRequired ? T.down : T.border}
        flexDirection="column"
        flexGrow={1}
        paddingLeft={1}
      >
        {a ? (
          <>
            <text fg={T.bright}>{clip(a.title, contentW - 60)}</text>
            <box flexDirection="row" gap={2}>
              <text fg={KIND_COLOR[a.kind]}>[{a.kind}]</text>
              <text fg={a.status === "PENDING" ? T.warn : a.status === "APPROVED" ? T.up : T.down}>◉ {a.status}</text>
              {a.signatureRequired && <text fg={T.down}>⚠ operator signature required</text>}
            </box>
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            {a.detailLines.map((l, i) => (
              <text key={i} fg={i === 0 ? T.fg : T.muted}>{clip(l, contentW - 60)}</text>
            ))}
            <text fg={T.borderBright}>{SEPARATOR.thin}</text>
            {a.status === "PENDING" ? (
              <>
                <text fg={T.dim}>your decision is journalled to operational_events and</text>
                <text fg={T.dim}>mirrored into the engine's autonomy history.</text>
                <box flexDirection="row" gap={2}>
                  <select
                    width="55%"
                    height={4}
                    focused
                    options={[
                      { name: "APPROVE", description: "", value: "approve" },
                      { name: "REJECT", description: "", value: "reject" },
                    ]}
                    showDescription={false}
                    showScrollIndicator={false}
                    selectedBackgroundColor={T.teal}
                    selectedTextColor={T.bg}
                    textColor={T.fg}
                    onSelect={(_i, opt) => {
                      const v = String(opt?.value ?? "")
                      if (v === "approve") decideApproval("APPROVED")
                      else if (v === "reject") decideApproval("REJECTED")
                    }}
                  />
                </box>
              </>
            ) : (
              <text fg={T.muted}>
                decided by {a.decidedBy} at {a.decidedTs ? fmtClock(a.decidedTs) : "—"}
              </text>
            )}
          </>
        ) : (
          <text fg={T.dim}>nothing selected</text>
        )}
      </box>
    </box>
  )
}
