"use client";

import Link from "next/link";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ButtonHTMLAttributes,
  type FormEvent,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { useParams } from "next/navigation";

export type JsonRecord = Record<string, unknown>;

export interface DashboardSummary {
  total_requisitions: number;
  open_requisitions: number;
  candidates: number;
  applications: number;
  interviews: number;
  offers: number;
  accepted_offers: number;
  rejected_offers: number;
  pending_offers: number;
  pipeline: PipelineStage[];
  scored_applications: number;
  average_screening_score: number | null;
  recorded_passes: number;
  recorded_fails: number;
  application_trend: ApplicationTrend[];
}

export interface PipelineStage {
  stage: string;
  count: number;
}

export interface ApplicationTrend {
  month: string;
  applications: number;
}

export interface ExceptionGroup {
  severity: string;
  issue_type: string;
  count: number;
}

export interface Requisition {
  requisition_id: string;
  department_id: string;
  title: string;
  description: string;
  employment_type: string;
  location: string;
  salary_min: string | number;
  salary_max: string | number;
  currency: string;
  headcount: number;
  status: string;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  opened_at: string | null;
  closed_at: string | null;
  created_at: string;
}

export interface Candidate {
  candidate_id: string;
  full_name: string;
  email: string;
  phone: string | null;
  location: string | null;
  professional_summary: string | null;
  portfolio_url: string | null;
  created_at: string;
}

export interface Application {
  application_id: string;
  requisition_id: string;
  candidate_id: string;
  status: string;
  submitted_at: string;
  screening_score: string | number | null;
  screening_explanation: JsonRecord | null;
  source: string | null;
  updated_at: string;
}

export interface ScreeningSkill {
  skill_id: string;
  name: string;
  proficiency_level: number | null;
  required: boolean;
}

export interface ScreeningResult {
  application_id: string;
  score: string | number;
  pass_threshold: string | number;
  required_skills_met: boolean;
  outcome: "pass" | "fail";
  matched_skills: ScreeningSkill[];
  missing_skills: ScreeningSkill[];
  criteria: JsonRecord[];
}

export interface Interview {
  interview_id: string;
  application_id: string;
  round_number: number;
  interview_type: string;
  starts_at: string;
  ends_at: string;
  status: string;
  outcome: string | null;
  issue_type: string | null;
  interviewer_ids: string[];
}

export interface Feedback {
  feedback_id: string;
  interview_id: string;
  interviewer_id: string;
  technical_rating: number;
  communication_rating: number;
  role_rating: number;
  recommendation: string;
  comments: string | null;
  submitted_at: string;
}

export interface Offer {
  offer_id: string;
  application_id: string;
  salary: string | number;
  currency: string;
  status: string;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  issued_at: string | null;
  responded_at: string | null;
}

export interface ExceptionRecord {
  exception_id: string;
  issue_type: string;
  entity_type: string;
  entity_id: string;
  expected_decision: string;
  observed_details: JsonRecord;
  validation_result: string;
  detected_at: string;
  severity: string;
  detected: boolean;
}

export interface AuditEvent {
  audit_event_id: string;
  entity_type: string;
  entity_id: string;
  action: string;
  actor: string;
  changes: JsonRecord | null;
  occurred_at: string;
}

interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly details: unknown;

  constructor(message: string, status: number, code?: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validationMessage(details: unknown): string | undefined {
  if (!isRecord(details) || !Array.isArray(details.errors)) {
    return undefined;
  }

  const messages = details.errors.flatMap((item: unknown) => {
    if (!isRecord(item) || typeof item.msg !== "string") {
      return [];
    }

    const location = Array.isArray(item.loc)
      ? item.loc.filter((part): part is string | number =>
          typeof part === "string" || typeof part === "number",
        )
      : [];
    const field = location.slice(1).join(".");
    return [field ? `${field}: ${item.msg}` : item.msg];
  });

  return messages.length > 0 ? messages.join("; ") : undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      cache: "no-store",
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(
      "Could not reach the backend through the frontend proxy. Check that FastAPI is running.",
      0,
    );
  }

  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const envelope = isRecord(body) ? (body as ApiErrorBody).error : undefined;
    const details = envelope?.details;
    const message =
      validationMessage(details) ??
      envelope?.message ??
      `The API request failed with status ${response.status}.`;
    throw new ApiError(message, response.status, envelope?.code, details);
  }

  return body as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  postWithoutBody: <T,>(path: string) => request<T>(path, { method: "POST" }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};

export async function listAll<T>(
  path: string,
  initialParams: Record<string, string> = {},
): Promise<T[]> {
  const pageSize = 500;
  const items: T[] = [];

  for (let offset = 0; ; offset += pageSize) {
    const params = new URLSearchParams({
      ...initialParams,
      limit: String(pageSize),
      offset: String(offset),
    });
    const page = await api.get<T[]>(`${path}?${params.toString()}`);
    items.push(...page);
    if (page.length < pageSize) {
      return items;
    }
  }
}

type SectionKey =
  | "dashboard"
  | "requisitions"
  | "candidates"
  | "applications"
  | "screening"
  | "interviews"
  | "feedback"
  | "offers"
  | "exceptions-audit";

interface PageData {
  summary?: DashboardSummary;
  pipeline?: PipelineStage[];
  exceptionGroups?: ExceptionGroup[];
  requisitions?: Requisition[];
  candidates?: Candidate[];
  applications?: Application[];
  interviews?: Interview[];
  offers?: Offer[];
  exceptions?: ExceptionRecord[];
  audit?: AuditEvent[];
}

const DASHBOARD_CACHE_TTL_MS = 15_000;
let dashboardSummaryCache: { value: DashboardSummary; loadedAt: number } | null = null;
let dashboardExceptionCache: { value: ExceptionGroup[]; loadedAt: number } | null = null;

function invalidateDashboardCache() {
  dashboardSummaryCache = null;
  dashboardExceptionCache = null;
}

const sectionInfo: Record<SectionKey, { title: string; subtitle: string }> = {
  dashboard: { title: "Overview", subtitle: "Recruitment activity at a glance" },
  requisitions: { title: "Requisitions", subtitle: "Manage approved hiring requests" },
  candidates: { title: "Candidates", subtitle: "Candidate profiles and applications" },
  applications: { title: "Applications", subtitle: "Track application stages and activity" },
  screening: { title: "Screening", subtitle: "Job-related screening results" },
  interviews: { title: "Interviews", subtitle: "Schedules and interviewer panels" },
  feedback: { title: "Feedback", subtitle: "Structured interviewer assessments" },
  offers: { title: "Offers", subtitle: "Offer approval and response tracking" },
  "exceptions-audit": { title: "Exceptions & audit", subtitle: "Validation findings and activity history" },
};

const applicationStatuses = [
  "applied",
  "screening",
  "interview",
  "offer",
  "hired",
  "rejected",
  "withdrawn",
];
const requisitionStatuses = ["draft", "pending_approval", "open", "on_hold", "closed"];
const employmentTypes = ["full_time", "part_time", "contract", "temporary"];
const interviewTypes = ["phone", "video", "onsite", "technical", "panel"];
const recommendations = ["strong_yes", "yes", "no", "strong_no"];
const PAGE_SIZE = 50;

function isSectionKey(value: string): value is SectionKey {
  return value in sectionInfo;
}

function displayLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value).replaceAll("_", " ");
}

function numberValue(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatCurrency(value: string | number, currency: string): string {
  const amount = numberValue(value);
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString()}`;
  }
}

function toLocalDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toIsoDateTime(value: string): string {
  return new Date(value).toISOString();
}

function errorText(error: unknown): string {
  if (!(error instanceof Error)) return "The request could not be completed.";
  if (error instanceof ApiError && isRecord(error.details) && isRecord(error.details.context)) {
    const context = Object.entries(error.details.context)
      .map(([key, value]) => `${displayLabel(key)}: ${isRecord(value) ? JSON.stringify(value) : String(value)}`)
      .join(", ");
    return context ? `${error.message} (${context})` : error.message;
  }
  return error.message;
}

function pageSlice<T>(items: T[], page: number): T[] {
  const start = (page - 1) * PAGE_SIZE;
  return items.slice(start, start + PAGE_SIZE);
}

function makeMonthlyTrend(monthlyCounts: ApplicationTrend[]) {
  const counts = new Map(monthlyCounts.map((item) => [item.month, item.applications]));
  const latestMonth = monthlyCounts.at(-1)?.month;
  if (!latestMonth) return [];

  const [year, month] = latestMonth.split("-").map(Number);
  const latest = new Date(year, month - 1, 1);
  return Array.from({ length: 6 }, (_, index) => {
    const date = new Date(latest.getFullYear(), latest.getMonth() - 5 + index, 1);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    return {
      month: new Intl.DateTimeFormat(undefined, { month: "short", year: "2-digit" }).format(date),
      applications: counts.get(key) ?? 0,
    };
  });
}

function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "quiet";
}) {
  const variants = {
    primary: "bg-blue-600 text-white shadow-sm shadow-blue-200 hover:bg-blue-700",
    secondary: "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
    danger: "bg-rose-50 text-rose-700 hover:bg-rose-100",
    quiet: "text-slate-600 hover:bg-slate-100",
  };
  return (
    <button
      className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

function TextField({
  label,
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-xs font-medium text-slate-600">{label}</span>
      <input
        className={`field-control ${className}`}
        {...props}
      />
    </label>
  );
}

function SelectField({
  label,
  children,
  className = "",
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string; children: ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-xs font-medium text-slate-600">{label}</span>
      <select className={`field-control ${className}`} {...props}>
        {children}
      </select>
    </label>
  );
}

function TextAreaField({
  label,
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-xs font-medium text-slate-600">{label}</span>
      <textarea className={`field-control min-h-24 resize-y ${className}`} {...props} />
    </label>
  );
}

function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-slate-200/80 bg-white shadow-sm shadow-slate-200/30 ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {action}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

function PageHeading({ section }: { section: SectionKey }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight text-slate-950">
          {sectionInfo[section].title}
        </h2>
        <p className="mt-1.5 text-sm text-slate-500">{sectionInfo[section].subtitle}</p>
      </div>
    </div>
  );
}

function StatusBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const tone =
    ["open", "accepted", "hired", "pass", "completed", "advance"].includes(normalized)
      ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
      :       ["rejected", "declined", "fail", "closed", "cancelled", "no_show", "high", "critical"].includes(normalized)
        ? "bg-rose-50 text-rose-700 ring-rose-100"
        : ["pending_approval", "on_hold", "screening", "issued", "medium", "low"].includes(normalized)
          ? "bg-amber-50 text-amber-700 ring-amber-100"
          : "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span className={`inline-flex whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold capitalize ring-1 ring-inset ${tone}`}>
      {displayLabel(value)}
    </span>
  );
}

function Table({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-left text-xs">
        <thead>
          <tr className="border-b border-slate-100 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            {headers.map((header) => (
              <th className="px-3 py-3 first:pl-0 last:pr-0" key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-5 py-10 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}

function Pagination({
  page,
  total,
  onChange,
}: {
  page: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (total <= PAGE_SIZE) return null;
  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
      <span className="text-[11px] text-slate-400">Showing {first}–{last} of {total}</span>
      <div className="flex items-center gap-2">
        <Button disabled={page <= 1} onClick={() => onChange(page - 1)} type="button" variant="secondary">Previous</Button>
        <span className="min-w-16 text-center text-[11px] text-slate-500">{page} / {pages}</span>
        <Button disabled={page >= pages} onClick={() => onChange(page + 1)} type="button" variant="secondary">Next</Button>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-200/30">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-slate-500">{label}</span>
        <span className={`h-2 w-2 rounded-full ${accent}`} />
      </div>
      <div className="mt-3 text-[28px] font-semibold leading-none tracking-tight text-slate-950">
        {value}
      </div>
    </div>
  );
}

function DetailCard({
  title,
  value,
  loading,
  error,
}: {
  title: string;
  value: unknown;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return <Panel title={title}><div className="animate-pulse text-sm text-slate-400">Loading details…</div></Panel>;
  }
  if (error) {
    return <Panel title={title}><div className="text-sm text-rose-700">{error}</div></Panel>;
  }
  if (value === null || value === undefined) return null;

  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index + 1), item] as const)
    : isRecord(value)
      ? Object.entries(value)
      : [["Value", value] as const];

  return (
    <Panel title={title}>
      {isRecord(value) && (Array.isArray(value.matched_skills) || Array.isArray(value.missing_skills)) ? (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <DetailValue label="Score" value={value.score} />
            <DetailValue label="Pass threshold" value={value.pass_threshold} />
            <DetailValue label="Outcome" value={value.outcome} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {(["matched_skills", "missing_skills"] as const).map((key) => (
              <div key={key}>
                <h3 className="mb-2 text-xs font-semibold capitalize text-slate-600">{displayLabel(key)}</h3>
                <div className="flex flex-wrap gap-2">
                  {(value[key] as unknown[]).map((skill, index) => (
                    <span className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs text-slate-600 ring-1 ring-inset ring-slate-200" key={index}>
                      {isRecord(skill) ? String(skill.name ?? skill.skill_id ?? "Skill") : String(skill)}
                    </span>
                  ))}
                  {(value[key] as unknown[]).length === 0 && <span className="text-xs text-slate-400">None</span>}
                </div>
              </div>
            ))}
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer font-medium text-slate-500">Screening criteria</summary>
            <pre className="mt-2 overflow-auto rounded-lg bg-slate-50 p-3 text-[11px] leading-5 text-slate-600">{JSON.stringify(value.criteria ?? [], null, 2)}</pre>
          </details>
        </div>
      ) : (
        <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
          {entries.map(([key, item]) => (
            <div className="min-w-0" key={key}>
              <dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">{displayLabel(key)}</dt>
              <dd className="mt-1 break-words text-xs leading-5 text-slate-700">
                {isRecord(item) || Array.isArray(item) ? (
                  <pre className="max-h-48 overflow-auto rounded-lg bg-slate-50 p-2 text-[10px] leading-4">{JSON.stringify(item, null, 2)}</pre>
                ) : displayLabel(item)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Panel>
  );
}

function DetailValue({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3.5 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">{label}</div>
      <div className="mt-1.5 text-sm font-semibold capitalize text-slate-800">{displayLabel(value)}</div>
    </div>
  );
}

function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return (
    <div className={`mb-4 rounded-xl border px-4 py-3 text-sm ${
      error
        ? "border-rose-200 bg-rose-50 text-rose-800"
        : "border-emerald-200 bg-emerald-50 text-emerald-800"
    }`}>
      {children}
    </div>
  );
}

const navigation = [
  { label: "Overview", href: "/", icon: "overview" },
  { label: "Requisitions", href: "/requisitions", icon: "requisitions" },
  { label: "Candidates", href: "/candidates", icon: "candidates" },
  { label: "Applications", href: "/applications", icon: "applications" },
  { label: "Screening", href: "/screening", icon: "screening" },
  { label: "Interviews", href: "/interviews", icon: "interviews" },
  { label: "Feedback", href: "/feedback", icon: "feedback" },
  { label: "Offers", href: "/offers", icon: "offers" },
  { label: "Exceptions & audit", href: "/exceptions-audit", icon: "audit" },
];

function NavigationIcon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    requisitions: <><path d="M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3" /><path d="M16 3h5v5" /><path d="m10 14 11-11" /><path d="M8 10h3" /><path d="M8 14h2" /><path d="M8 18h6" /></>,
    candidates: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-1a6 6 0 0 1 12 0v1" /><path d="M16 4.5a3 3 0 0 1 0 7" /><path d="M18 14a5 5 0 0 1 3 5v1" /></>,
    applications: <><path d="M8 4h11a2 2 0 0 1 2 2v14H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h1" /><path d="M8 2h8v4H8z" /><path d="M8 11h9M8 15h9" /></>,
    screening: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /><path d="M8 11h6M11 8v6" /></>,
    interviews: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /><path d="m9 15 2 2 4-4" /></>,
    feedback: <><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 9 9 0 0 1-4-.9L3 21l1.9-5.5a9 9 0 0 1-.9-4A8.5 8.5 0 0 1 12.5 3h.5a8.5 8.5 0 0 1 8 8v.5Z" /><path d="m8 12 2.5 2.5L16 9" /></>,
    offers: <><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></>,
    audit: <><path d="M12 3 4 6v5c0 5 3.4 8.7 8 10 4.6-1.3 8-5 8-10V6l-8-3Z" /><path d="M9 12h6M12 9v6" /></>,
  };

  return (
    <svg
      aria-hidden="true"
      className="h-[18px] w-[18px] shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
      viewBox="0 0 24 24"
    >
      {paths[name]}
    </svg>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main navigation" className="space-y-1">
      {navigation.map((item) => {
        return (
          <Link
            className="group flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium text-slate-600 transition hover:bg-slate-50 hover:text-slate-950"
            href={item.href}
            key={item.href}
            onClick={onNavigate}
          >
            <NavigationIcon name={item.icon} />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .get<{ status?: string }>("/health")
      .then(() => active && setApiOnline(true))
      .catch(() => active && setApiOnline(false));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="min-h-screen bg-[#f5f7fb] text-slate-900">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[252px] flex-col border-r border-slate-200/80 bg-white px-4 py-6 lg:flex">
        <Link className="mb-9 flex items-center gap-3 px-2" href="/">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-sm font-bold tracking-tight text-white shadow-sm shadow-blue-200">
            RM
          </span>
          <span>
            <span className="block text-[15px] font-semibold tracking-tight">Recruitment</span>
            <span className="mt-0.5 block text-[11px] text-slate-400">Management system</span>
          </span>
        </Link>
        <div className="mb-3 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
          Workspace
        </div>
        <NavLinks />
        <div className="mt-auto rounded-xl border border-slate-100 bg-slate-50 px-3.5 py-3">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${apiOnline ? "bg-emerald-500" : apiOnline === false ? "bg-rose-500" : "bg-slate-300"}`} />
            <span className="text-xs font-medium text-slate-700">
              {apiOnline ? "API connected" : apiOnline === false ? "API unavailable" : "Connecting"}
            </span>
          </div>
          <p className="mt-1.5 pl-4 text-[11px] leading-4 text-slate-400">
            Live backend connection
          </p>
        </div>
      </aside>

      <div className="lg:pl-[252px]">
        <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/90 backdrop-blur-md">
          <div className="flex h-[68px] items-center justify-between px-4 sm:px-7 lg:px-9">
            <div className="flex items-center gap-3">
              <button
                aria-expanded={mobileMenuOpen}
                aria-label={mobileMenuOpen ? "Close navigation" : "Open navigation"}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 lg:hidden"
                onClick={() => setMobileMenuOpen((open) => !open)}
                type="button"
              >
                <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
                  {mobileMenuOpen ? <path d="m6 6 12 12M18 6 6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
                </svg>
              </button>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-slate-400">
                  Recruitment workspace
                </div>
                <h1 className="mt-0.5 text-[15px] font-semibold tracking-tight text-slate-900">Recruitment</h1>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className={`hidden items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium sm:flex ${
                apiOnline ? "bg-emerald-50 text-emerald-700" : apiOnline === false ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-500"
              }`}>
                <span className={`h-1.5 w-1.5 rounded-full ${apiOnline ? "bg-emerald-500" : apiOnline === false ? "bg-rose-500" : "bg-slate-400"}`} />
                {apiOnline ? "Connected" : apiOnline === false ? "Disconnected" : "Checking"}
              </span>
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">
                RM
              </div>
            </div>
          </div>
          {mobileMenuOpen && (
            <div className="border-t border-slate-100 bg-white px-4 py-3 lg:hidden">
              <NavLinks onNavigate={() => setMobileMenuOpen(false)} />
            </div>
          )}
        </header>
        <main className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-7 sm:py-8 lg:px-9">
          {children}
        </main>
      </div>
    </div>
  );
}

function Workspace({ section: requestedSection }: { section: string }) {
  const section = isSectionKey(requestedSection) ? requestedSection : null;
  const [data, setData] = useState<PageData>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [detailUrl, setDetailUrl] = useState<string | null>(null);
  const [detail, setDetail] = useState<unknown>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [relatedApplications, setRelatedApplications] = useState<Application[]>([]);
  const [relatedLoading, setRelatedLoading] = useState(false);
  const [relatedError, setRelatedError] = useState<string | null>(null);
  const [dashboardExceptionLoading, setDashboardExceptionLoading] = useState(false);
  const [dashboardExceptionError, setDashboardExceptionError] = useState<string | null>(null);
  const [dashboardUpdatedAt, setDashboardUpdatedAt] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [exceptionPage, setExceptionPage] = useState(1);
  const [auditPage, setAuditPage] = useState(1);

  useEffect(() => {
    if (!section) return;
    let active = true;
    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        let next: PageData = {};
        switch (section) {
          case "dashboard": {
            const cached = dashboardSummaryCache;
            if (cached && Date.now() - cached.loadedAt < DASHBOARD_CACHE_TTL_MS) {
              next = { summary: cached.value, pipeline: cached.value.pipeline };
              setDashboardUpdatedAt(cached.loadedAt);
            } else {
              const summary = await api.get<DashboardSummary>("/api/dashboard/summary");
              const loadedAt = Date.now();
              dashboardSummaryCache = { value: summary, loadedAt };
              next = { summary, pipeline: summary.pipeline };
              setDashboardUpdatedAt(loadedAt);
            }
            break;
          }
          case "requisitions":
            next = { requisitions: await listAll<Requisition>("/api/requisitions") };
            break;
          case "candidates":
            next = { candidates: await listAll<Candidate>("/api/candidates") };
            break;
          case "applications":
          case "screening":
            next = { applications: await listAll<Application>("/api/applications") };
            break;
          case "interviews":
          case "feedback":
            next = { interviews: await listAll<Interview>("/api/interviews") };
            break;
          case "offers":
            next = { offers: await listAll<Offer>("/api/offers") };
            break;
          case "exceptions-audit": {
            const [exceptions, audit] = await Promise.all([
              listAll<ExceptionRecord>("/api/exceptions"),
              api.get<AuditEvent[]>("/api/audit?limit=100&offset=0"),
            ]);
            next = { exceptions, audit };
            break;
          }
        }
        if (active) {
          setData((current) => section === "dashboard" ? { ...current, ...next } : next);
        }
      } catch (error) {
        if (active) setLoadError(errorText(error));
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [section, refreshKey]);

  useEffect(() => {
    if (section !== "dashboard") return;
    let active = true;
    async function loadExceptionGroups() {
      const cached = dashboardExceptionCache;
      if (cached && Date.now() - cached.loadedAt < DASHBOARD_CACHE_TTL_MS) {
        setData((current) => ({ ...current, exceptionGroups: cached.value }));
        setDashboardExceptionError(null);
        setDashboardExceptionLoading(false);
        return;
      }

      setDashboardExceptionLoading(true);
      setDashboardExceptionError(null);
      try {
        const groups = await api.get<ExceptionGroup[]>("/api/dashboard/exceptions");
        if (!active) return;
        dashboardExceptionCache = { value: groups, loadedAt: Date.now() };
        setData((current) => ({ ...current, exceptionGroups: groups }));
      } catch (error) {
        if (active) setDashboardExceptionError(errorText(error));
      } finally {
        if (active) setDashboardExceptionLoading(false);
      }
    }

    void loadExceptionGroups();

    return () => {
      active = false;
    };
  }, [section, refreshKey]);

  useEffect(() => {
    if (!detailUrl) return;
    let active = true;
    api
      .get<unknown>(detailUrl)
      .then((result) => active && setDetail(result))
      .catch((error: unknown) => active && setDetailError(errorText(error)))
      .finally(() => active && setDetailLoading(false));
    return () => {
      active = false;
    };
  }, [detailUrl, refreshKey]);

  useEffect(() => {
    if (section !== "candidates" || !selectedId) return;
    let active = true;
    api
      .get<Application[]>(`/api/applications?candidate_id=${encodeURIComponent(selectedId)}&limit=500&offset=0`)
      .then((items) => active && setRelatedApplications(items))
      .catch((error: unknown) => active && setRelatedError(errorText(error)))
      .finally(() => active && setRelatedLoading(false));
    return () => {
      active = false;
    };
  }, [section, selectedId, refreshKey]);

  const perform = useCallback(async (operation: () => Promise<unknown>, successMessage: string) => {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    setPage(1);
    setExceptionPage(1);
    setAuditPage(1);
    if (detailUrl) setDetailLoading(true);
    if (section === "candidates" && selectedId) setRelatedLoading(true);
    try {
      await operation();
      invalidateDashboardCache();
      setNotice(successMessage);
      setRefreshKey((key) => key + 1);
      return true;
    } catch (error) {
      setActionError(errorText(error));
      return false;
    } finally {
      setBusy(false);
    }
  }, [detailUrl, section, selectedId]);

  const selectResource = useCallback((id: string, url: string) => {
    setSelectedId(id);
    setDetailUrl(url);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    if (section === "candidates") {
      setRelatedApplications([]);
      setRelatedLoading(true);
      setRelatedError(null);
    }
  }, [section]);

  const selectSnapshot = useCallback((id: string, value: unknown) => {
    setSelectedId(id);
    setDetailUrl(null);
    setDetail(value);
    setDetailError(null);
    setDetailLoading(false);
  }, []);

  const onSubmit = useCallback((
    event: FormEvent<HTMLFormElement>,
    operation: () => Promise<unknown>,
    successMessage: string,
  ): Promise<boolean> => {
    event.preventDefault();
    return perform(operation, successMessage);
  }, [perform]);

  const content = useMemo(() => {
    if (!section) return null;
    const filterText = search.trim().toLowerCase();
    const matchesText = (...values: unknown[]) =>
      !filterText || values.some((value) => String(value ?? "").toLowerCase().includes(filterText));

    if (section === "dashboard") {
      const summary = data.summary;
      const exceptionGroups = data.exceptionGroups ?? [];
      const exceptionCount = exceptionGroups.reduce((sum, item) => sum + item.count, 0);
      const trend = makeMonthlyTrend(summary?.application_trend ?? []);

      return (
        <>
          <PageHeading section={section} />
          {dashboardUpdatedAt !== null && (
            <p className="mb-3 text-xs text-slate-400">
              Data updated {new Date(dashboardUpdatedAt).toLocaleTimeString()}
            </p>
          )}
          {loadError && <Notice error>{loadError}</Notice>}
          {loading && <EmptyState>Loading dashboard data…</EmptyState>}
          {!loading && summary && (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <MetricCard accent="bg-blue-500" label="Requisitions" value={summary.total_requisitions} />
                <MetricCard accent="bg-violet-500" label="Candidates" value={summary.candidates} />
                <MetricCard accent="bg-cyan-500" label="Applications" value={summary.applications} />
                <MetricCard accent="bg-amber-500" label="Interviews" value={summary.interviews} />
                <MetricCard accent="bg-emerald-500" label="Offers" value={summary.offers} />
              </div>

              <div className="mt-4 grid gap-4 xl:grid-cols-[1.05fr_1.4fr]">
                <Panel title="Application pipeline">
                  {(data.pipeline ?? []).length === 0 ? (
                    <EmptyState>No application stages are available.</EmptyState>
                  ) : (
                    <div className="h-[280px] w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data.pipeline} layout="vertical" margin={{ left: 8, right: 18, top: 4, bottom: 4 }}>
                          <CartesianGrid stroke="#eef2f7" horizontal={false} />
                          <XAxis allowDecimals={false} axisLine={false} tickLine={false} type="number" tick={{ fill: "#94a3b8", fontSize: 11 }} />
                          <YAxis axisLine={false} tickLine={false} type="category" dataKey="stage" width={90} tick={{ fill: "#64748b", fontSize: 11 }} tickFormatter={(value: string) => displayLabel(value)} />
                          <Tooltip formatter={(value) => [value, "Applications"]} />
                          <Bar dataKey="count" fill="#4f6df5" radius={[0, 5, 5, 0]} barSize={16} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Panel>
                <Panel title="Application activity">
                  {trend.length === 0 ? (
                    <EmptyState>No application dates are available.</EmptyState>
                  ) : (
                    <div className="h-[280px] w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={trend} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                          <defs>
                            <linearGradient id="applicationsFill" x1="0" x2="0" y1="0" y2="1">
                              <stop offset="0%" stopColor="#6381ff" stopOpacity={0.22} />
                              <stop offset="100%" stopColor="#6381ff" stopOpacity={0.01} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid stroke="#eef2f7" vertical={false} />
                          <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                          <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                          <Tooltip />
                          <Area dataKey="applications" type="monotone" stroke="#4f6df5" strokeWidth={2.5} fill="url(#applicationsFill)" />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Panel>
              </div>

              <div className="mt-4 grid gap-4 xl:grid-cols-[1.2fr_1fr]">
                <Panel title="Screening coverage">
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <MetricCard accent="bg-indigo-500" label="Scored applications" value={summary.scored_applications} />
                    <MetricCard accent="bg-sky-500" label="Average score" value={summary.average_screening_score === null ? "—" : summary.average_screening_score.toFixed(1)} />
                    <MetricCard accent="bg-emerald-500" label="Recorded passes" value={summary.recorded_passes} />
                    <MetricCard accent="bg-rose-500" label="Recorded fails" value={summary.recorded_fails} />
                  </div>
                  <p className="mt-3 text-[11px] text-slate-400">Pass and fail totals include only outcomes explicitly returned by the API.</p>
                </Panel>
                <Panel title="Offers and exceptions">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <MetricCard accent="bg-emerald-500" label="Accepted offers" value={summary.accepted_offers} />
                    <MetricCard accent="bg-rose-500" label="Rejected offers" value={summary.rejected_offers} />
                    <MetricCard accent="bg-amber-500" label="Pending offers" value={summary.pending_offers} />
                    <MetricCard
                      accent="bg-orange-500"
                      label="Detected exceptions"
                      value={dashboardExceptionLoading ? "…" : dashboardExceptionError ? "—" : exceptionCount}
                    />
                  </div>
                  {dashboardExceptionLoading && <p className="mt-3 text-xs text-slate-400">Loading exception summary…</p>}
                  {dashboardExceptionError && <Notice error>{dashboardExceptionError}</Notice>}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {exceptionGroups.map((group) => (
                      <span className="rounded-full bg-slate-50 px-2.5 py-1 text-[11px] capitalize text-slate-600 ring-1 ring-inset ring-slate-200" key={`${group.severity}-${group.issue_type}`}>
                        {displayLabel(group.severity)} · {displayLabel(group.issue_type)} · {group.count}
                      </span>
                    ))}
                    {!dashboardExceptionLoading && !dashboardExceptionError && exceptionGroups.length === 0 && <span className="text-xs text-slate-400">No exceptions detected.</span>}
                  </div>
                </Panel>
              </div>
            </>
          )}
        </>
      );
    }

    if (section === "requisitions") {
      const rows = (data.requisitions ?? []).filter((item) =>
        matchesText(item.requisition_id, item.title, item.department_id, item.location) &&
        (!statusFilter || item.status === statusFilter),
      );
      const selected = rows.find((item) => item.requisition_id === selectedId);
      return (
        <>
          <PageHeading section={section} />
          {notice && <Notice>{notice}</Notice>}
          {actionError && <Notice error>{actionError}</Notice>}
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel className="mb-4" title="Create requisition">
            <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" onSubmit={(event) => {
              const form = event.currentTarget;
              const values = new FormData(form);
              const requiredSkillId = String(values.get("required_skill_id") ?? "").trim();
              const requiredRule: Record<string, unknown> = {
                skill_id: requiredSkillId,
                rule_type: "required_skill",
                criterion_name: `Required skill ${requiredSkillId}`,
                weight: Number(values.get("required_weight")),
              };
              const proficiency = String(values.get("required_proficiency") ?? "");
              if (proficiency) requiredRule.minimum_proficiency = Number(proficiency);
              const screeningRules: Record<string, unknown>[] = [requiredRule];
              const weightedSkillId = String(values.get("weighted_skill_id") ?? "").trim();
              if (weightedSkillId) {
                const weightedRule: Record<string, unknown> = {
                  skill_id: weightedSkillId,
                  rule_type: "weighted_skill",
                  criterion_name: `Weighted skill ${weightedSkillId}`,
                  weight: Number(values.get("weighted_weight")),
                };
                const weightedProficiency = String(values.get("weighted_proficiency") ?? "");
                if (weightedProficiency) weightedRule.minimum_proficiency = Number(weightedProficiency);
                screeningRules.push(weightedRule);
              }
              const threshold = String(values.get("pass_threshold") ?? "");
              if (threshold) {
                screeningRules.push({
                  rule_type: "pass_threshold",
                  criterion_name: "Pass threshold",
                  weight: 0,
                  minimum_value: Number(threshold),
                });
              }
              void onSubmit(event, () => api.post<Requisition>("/api/requisitions", {
                department_id: String(values.get("department_id")),
                title: String(values.get("title")),
                description: String(values.get("description")),
                employment_type: String(values.get("employment_type")),
                location: String(values.get("location")),
                salary_min: Number(values.get("salary_min")),
                salary_max: Number(values.get("salary_max")),
                currency: String(values.get("currency")).toUpperCase(),
                headcount: Number(values.get("headcount")),
                created_by: String(values.get("created_by")),
                screening_rules: screeningRules,
              }), "Requisition created.").then((success) => {
                if (success) form.reset();
              });
            }}>
              <TextField label="Department ID" name="department_id" required />
              <TextField label="Title" name="title" required />
              <TextField label="Location" name="location" required />
              <SelectField label="Employment type" name="employment_type" required>
                {employmentTypes.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
              </SelectField>
              <TextField label="Salary minimum" min="0" name="salary_min" required step="0.01" type="number" />
              <TextField label="Salary maximum" min="0.01" name="salary_max" required step="0.01" type="number" />
              <TextField defaultValue="USD" label="Currency" maxLength={3} name="currency" required />
              <TextField defaultValue="1" label="Headcount" min="1" name="headcount" required type="number" />
              <TextField defaultValue="recruiter" label="Created by" name="created_by" required />
              <TextField label="Required skill ID" name="required_skill_id" required />
              <TextField label="Required skill weight" min="0.01" name="required_weight" required step="0.01" type="number" />
              <SelectField label="Minimum proficiency" name="required_proficiency">
                <option value="">Not specified</option>
                {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}
              </SelectField>
              <TextField label="Additional skill ID" name="weighted_skill_id" />
              <TextField label="Additional skill weight" min="0" name="weighted_weight" step="0.01" type="number" />
              <SelectField label="Additional skill proficiency" name="weighted_proficiency">
                <option value="">Not specified</option>
                {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}
              </SelectField>
              <TextField label="Pass threshold" max="100" min="0" name="pass_threshold" step="0.01" type="number" />
              <div className="sm:col-span-2 xl:col-span-3">
                <TextAreaField label="Description" name="description" required />
              </div>
              <div className="sm:col-span-2 xl:col-span-3">
                <Button disabled={busy} type="submit">{busy ? "Saving…" : "Create requisition"}</Button>
              </div>
            </form>
            <p className="mt-3 text-[11px] text-slate-400">Enter existing department and skill IDs; the API validates them.</p>
          </Panel>

          <Panel title="Requisition list" action={<span className="text-xs text-slate-400">{rows.length} records</span>}>
            <div className="mb-4 flex flex-wrap gap-2">
              <TextField aria-label="Search requisitions" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Title, ID, department…" value={search} />
              <SelectField label="Status" onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }} value={statusFilter}>
                <option value="">All statuses</option>
                {requisitionStatuses.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
              </SelectField>
            </div>
            {loading ? <EmptyState>Loading requisitions…</EmptyState> : rows.length === 0 ? <EmptyState>No requisitions match this view.</EmptyState> : (
              <Table headers={["Requisition", "Department", "Location", "Salary band", "Status", ""]}>
                {pageSlice(rows, page).map((item) => (
                  <tr className="hover:bg-slate-50/70" key={item.requisition_id}>
                    <td className="px-3 py-3 first:pl-0">
                      <div className="font-semibold text-slate-800">{item.title}</div>
                      <div className="mt-1 text-[10px] text-slate-400">{item.requisition_id}</div>
                    </td>
                    <td className="px-3 py-3 text-slate-600">{item.department_id}</td>
                    <td className="px-3 py-3 text-slate-600">{item.location}</td>
                    <td className="px-3 py-3 text-slate-600">{formatCurrency(item.salary_min, item.currency)} – {formatCurrency(item.salary_max, item.currency)}</td>
                    <td className="px-3 py-3"><StatusBadge value={item.status} /></td>
                    <td className="px-3 py-3 text-right last:pr-0">
                      <Button onClick={() => selectResource(item.requisition_id, `/api/requisitions/${encodeURIComponent(item.requisition_id)}`)} type="button" variant="secondary">Details</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>

          {selected && (
            <div className="mt-4 space-y-4">
              <DetailCard error={detailError} loading={detailLoading} title={`Requisition · ${selected.requisition_id}`} value={detail} />
              <Panel title="Requisition actions">
                <div className="space-y-4">
                  {selected.status === "draft" && (
                    <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
                      const values = new FormData(event.currentTarget);
                      onSubmit(event, () => api.post(`/api/requisitions/${encodeURIComponent(selected.requisition_id)}/approve`, {
                        actor: String(values.get("actor")),
                        reason: String(values.get("reason") || "") || null,
                      }), "Requisition approved.");
                    }}>
                      <TextField defaultValue="recruiter" label="Approver" name="actor" required />
                      <TextField label="Reason" name="reason" />
                      <Button disabled={busy} type="submit">Approve</Button>
                    </form>
                  )}
                  {["open", "on_hold"].includes(selected.status) && (
                    <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
                      const values = new FormData(event.currentTarget);
                      onSubmit(event, () => api.post(`/api/requisitions/${encodeURIComponent(selected.requisition_id)}/close`, {
                        actor: String(values.get("actor")),
                        reason: String(values.get("reason") || "") || null,
                      }), "Requisition closed.");
                    }}>
                      <TextField defaultValue="recruiter" label="Actor" name="actor" required />
                      <TextField label="Reason" name="reason" />
                      <Button disabled={busy} type="submit" variant="secondary">Close</Button>
                    </form>
                  )}
                  {!["draft", "open", "on_hold"].includes(selected.status) && <span className="text-xs text-slate-500">No state change is available for this requisition.</span>}
                </div>
                <form className="mt-5 grid gap-3 sm:grid-cols-2" onSubmit={(event) => {
                  const values = new FormData(event.currentTarget);
                  const patch = Object.fromEntries(
                    ["title", "description", "location", "salary_min", "salary_max", "headcount"]
                      .map((key) => [key, values.get(key)])
                      .filter(([, value]) => value !== null && value !== ""),
                  );
                  const form = event.currentTarget;
                  void onSubmit(event, () => api.patch(`/api/requisitions/${encodeURIComponent(selected.requisition_id)}`, {
                    ...patch,
                    ...(patch.salary_min ? { salary_min: Number(patch.salary_min) } : {}),
                    ...(patch.salary_max ? { salary_max: Number(patch.salary_max) } : {}),
                    ...(patch.headcount ? { headcount: Number(patch.headcount) } : {}),
                    updated_by: String(values.get("updated_by") || "recruiter"),
                  }), "Requisition updated.").then((success) => {
                    if (success) form.reset();
                  });
                }}>
                  <TextField label="Title" name="title" placeholder="Leave blank to keep current" />
                  <TextField label="Location" name="location" placeholder="Leave blank to keep current" />
                  <TextField label="Salary minimum" min="0" name="salary_min" step="0.01" type="number" />
                  <TextField label="Salary maximum" min="0" name="salary_max" step="0.01" type="number" />
                  <TextField label="Headcount" min="1" name="headcount" type="number" />
                  <TextField label="Updated by" name="updated_by" defaultValue="recruiter" />
                  <div className="sm:col-span-2">
                    <TextAreaField label="Description" name="description" placeholder="Leave blank to keep current" />
                  </div>
                  <div className="sm:col-span-2">
                    <Button disabled={busy || !["draft", "pending_approval"].includes(selected.status)} type="submit" variant="secondary">Save changes</Button>
                  </div>
                </form>
              </Panel>
            </div>
          )}
        </>
      );
    }

    if (section === "candidates") {
      const locations = Array.from(
        new Set((data.candidates ?? []).map((item) => item.location).filter((value): value is string => Boolean(value))),
      ).sort();
      const rows = (data.candidates ?? []).filter((item) =>
        matchesText(item.candidate_id, item.full_name, item.email, item.location) &&
        (!statusFilter || item.location === statusFilter),
      );
      const selected = rows.find((item) => item.candidate_id === selectedId);
      return (
        <>
          <PageHeading section={section} />
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel title="Candidate directory" action={<span className="text-xs text-slate-400">{rows.length} records</span>}>
            <div className="mb-4 flex flex-wrap gap-2">
              <TextField aria-label="Search candidates" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Name, email, location…" value={search} />
              <SelectField label="Location" onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }} value={statusFilter}>
                <option value="">All locations</option>
                {locations.map((location) => <option key={location} value={location}>{location}</option>)}
              </SelectField>
            </div>
            {loading ? <EmptyState>Loading candidates…</EmptyState> : rows.length === 0 ? <EmptyState>No candidates match this search.</EmptyState> : (
              <Table headers={["Candidate", "Email", "Location", "Created", ""]}>
                {pageSlice(rows, page).map((item) => (
                  <tr className="hover:bg-slate-50/70" key={item.candidate_id}>
                    <td className="px-3 py-3 first:pl-0">
                      <div className="font-semibold text-slate-800">{item.full_name}</div>
                      <div className="mt-1 text-[10px] text-slate-400">{item.candidate_id}</div>
                    </td>
                    <td className="px-3 py-3 text-slate-600">{item.email}</td>
                    <td className="px-3 py-3 text-slate-600">{item.location ?? "—"}</td>
                    <td className="px-3 py-3 text-slate-500">{formatDate(item.created_at)}</td>
                    <td className="px-3 py-3 text-right last:pr-0">
                      <Button onClick={() => selectResource(item.candidate_id, `/api/candidates/${encodeURIComponent(item.candidate_id)}`)} type="button" variant="secondary">Details</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>
          {selected && (
            <div className="mt-4 space-y-4">
              <DetailCard error={detailError} loading={detailLoading} title={`Candidate · ${selected.full_name}`} value={detail} />
              <Panel title="Applications">
                {relatedError ? <Notice error>{relatedError}</Notice> : relatedLoading ? <EmptyState>Loading applications…</EmptyState> : relatedApplications.length === 0 ? <EmptyState>No applications for this candidate.</EmptyState> : (
                  <Table headers={["Application", "Requisition", "Submitted", "Stage"]}>
                    {relatedApplications.map((item) => (
                      <tr key={item.application_id}>
                        <td className="px-3 py-3 first:pl-0 font-medium text-slate-700">{item.application_id}</td>
                        <td className="px-3 py-3 text-slate-600">{item.requisition_id}</td>
                        <td className="px-3 py-3 text-slate-500">{formatDate(item.submitted_at)}</td>
                        <td className="px-3 py-3 last:pr-0"><StatusBadge value={item.status} /></td>
                      </tr>
                    ))}
                  </Table>
                )}
              </Panel>
            </div>
          )}
        </>
      );
    }

    if (section === "applications") {
      const rows = (data.applications ?? []).filter((item) =>
        matchesText(item.application_id, item.candidate_id, item.requisition_id, item.source) &&
        (!statusFilter || item.status === statusFilter),
      );
      const selected = rows.find((item) => item.application_id === selectedId);
      return (
        <>
          <PageHeading section={section} />
          {notice && <Notice>{notice}</Notice>}
          {actionError && <Notice error>{actionError}</Notice>}
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel className="mb-4" title="Create application">
            <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => {
              const form = event.currentTarget;
              const values = new FormData(event.currentTarget);
              void onSubmit(event, () => api.post<Application>("/api/applications", {
                candidate_id: String(values.get("candidate_id")),
                requisition_id: String(values.get("requisition_id")),
                source: String(values.get("source") || "") || null,
              }), "Application submitted.").then((success) => {
                if (success) form.reset();
              });
            }}>
              <TextField label="Candidate ID" name="candidate_id" required />
              <TextField label="Requisition ID" name="requisition_id" required />
              <TextField label="Source" name="source" placeholder="Optional" />
              <div className="flex items-end"><Button disabled={busy} type="submit">Submit application</Button></div>
            </form>
            <p className="mt-3 text-[11px] text-slate-400">Duplicate applications and requisition eligibility are checked by the API.</p>
          </Panel>
          <Panel title="Application register" action={<span className="text-xs text-slate-400">{rows.length} records</span>}>
            <div className="mb-4 flex flex-wrap gap-2">
              <TextField aria-label="Search applications" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Application, candidate, requisition…" value={search} />
              <SelectField label="Stage" onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }} value={statusFilter}>
                <option value="">All stages</option>
                {applicationStatuses.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
              </SelectField>
            </div>
            {loading ? <EmptyState>Loading applications…</EmptyState> : rows.length === 0 ? <EmptyState>No applications match this view.</EmptyState> : (
              <Table headers={["Application", "Candidate", "Requisition", "Submitted", "Stage", ""]}>
                {pageSlice(rows, page).map((item) => (
                  <tr className="hover:bg-slate-50/70" key={item.application_id}>
                    <td className="px-3 py-3 first:pl-0 font-semibold text-slate-800">{item.application_id}</td>
                    <td className="px-3 py-3 text-slate-600">{item.candidate_id}</td>
                    <td className="px-3 py-3 text-slate-600">{item.requisition_id}</td>
                    <td className="px-3 py-3 text-slate-500">{formatDate(item.submitted_at)}</td>
                    <td className="px-3 py-3"><StatusBadge value={item.status} /></td>
                    <td className="px-3 py-3 text-right last:pr-0">
                      <Button onClick={() => selectResource(item.application_id, `/api/applications/${encodeURIComponent(item.application_id)}`)} type="button" variant="secondary">Details</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>
          {selected && (
            <div className="mt-4 space-y-4">
              <DetailCard error={detailError} loading={detailLoading} title={`Application · ${selected.application_id}`} value={detail} />
              <Panel title="Advance application stage">
                <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => {
                  const values = new FormData(event.currentTarget);
                  onSubmit(event, () => api.post<Application>(`/api/applications/${encodeURIComponent(selected.application_id)}/advance`, {
                    to_status: String(values.get("to_status")),
                    actor: String(values.get("actor")),
                    reason: String(values.get("reason") || "") || null,
                  }), "Stage change submitted.");
                }}>
                  <SelectField label="Target stage" name="to_status" required>
                    {applicationStatuses.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
                  </SelectField>
                  <TextField defaultValue="recruiter" label="Actor" name="actor" required />
                  <TextField label="Reason" name="reason" placeholder="Required for rejection or withdrawal" />
                  <div className="flex items-end"><Button disabled={busy} type="submit">Submit stage change</Button></div>
                </form>
                <p className="mt-3 text-[11px] text-slate-400">The API validates every transition and its screening, interview, and offer prerequisites.</p>
              </Panel>
            </div>
          )}
        </>
      );
    }

    if (section === "screening") {
      const rows = (data.applications ?? []).filter((item) =>
        matchesText(item.application_id, item.candidate_id, item.requisition_id, item.status),
      );
      return (
        <>
          <PageHeading section={section} />
          {actionError && <Notice error>{actionError}</Notice>}
          {notice && <Notice>{notice}</Notice>}
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel title="Screening results" action={<span className="text-xs text-slate-400">{rows.length} applications</span>}>
            <div className="mb-4 max-w-sm">
              <TextField aria-label="Search screening results" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Application, candidate, requisition…" value={search} />
            </div>
            {loading ? <EmptyState>Loading screening data…</EmptyState> : rows.length === 0 ? <EmptyState>No applications are available.</EmptyState> : (
              <Table headers={["Application", "Candidate", "Requisition", "Score", "Stage", ""]}>
                {pageSlice(rows, page).map((item) => {
                  const outcome = item.screening_explanation?.outcome;
                  return (
                    <tr className="hover:bg-slate-50/70" key={item.application_id}>
                      <td className="px-3 py-3 first:pl-0 font-semibold text-slate-800">{item.application_id}</td>
                      <td className="px-3 py-3 text-slate-600">{item.candidate_id}</td>
                      <td className="px-3 py-3 text-slate-600">{item.requisition_id}</td>
                      <td className="px-3 py-3 text-slate-600">{item.screening_score ?? "—"}{outcome === "pass" || outcome === "fail" ? <span className="ml-2"><StatusBadge value={outcome} /></span> : null}</td>
                      <td className="px-3 py-3"><StatusBadge value={item.status} /></td>
                      <td className="px-3 py-3 text-right last:pr-0">
                        <div className="flex justify-end gap-2">
                          <Button onClick={() => selectResource(item.application_id, `/api/applications/${encodeURIComponent(item.application_id)}/screening`)} type="button" variant="secondary">View</Button>
                          <Button disabled={busy} onClick={() => {
                            selectResource(item.application_id, `/api/applications/${encodeURIComponent(item.application_id)}/screening`);
                            void perform(
                              () =>                               api.postWithoutBody<ScreeningResult>(`/api/applications/${encodeURIComponent(item.application_id)}/screen`),
                              "Screening completed.",
                            );
                          }} type="button">Run</Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>
          {selectedId && <div className="mt-4"><DetailCard error={detailError} loading={detailLoading} title={`Screening · ${selectedId}`} value={detail} /></div>}
        </>
      );
    }

    if (section === "interviews") {
      const rows = (data.interviews ?? []).filter((item) =>
        matchesText(item.interview_id, item.application_id, item.interviewer_ids.join(" "), item.status),
      );
      const selected = rows.find((item) => item.interview_id === selectedId);
      return (
        <>
          <PageHeading section={section} />
          {notice && <Notice>{notice}</Notice>}
          {actionError && <Notice error>{actionError}</Notice>}
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel className="mb-4" title="Schedule interview">
            <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" onSubmit={(event) => {
              const form = event.currentTarget;
              const values = new FormData(event.currentTarget);
              void onSubmit(event, () => api.post<Interview>("/api/interviews", {
                application_id: String(values.get("application_id")),
                round_number: Number(values.get("round_number")),
                interview_type: String(values.get("interview_type")),
                starts_at: toIsoDateTime(String(values.get("starts_at"))),
                ends_at: toIsoDateTime(String(values.get("ends_at"))),
                interviewer_ids: String(values.get("interviewer_ids")).split(",").map((id) => id.trim()).filter(Boolean),
              }), "Interview scheduled.").then((success) => {
                if (success) form.reset();
              });
            }}>
              <TextField label="Application ID" name="application_id" required />
              <TextField defaultValue="1" label="Round" min="1" name="round_number" required type="number" />
              <SelectField label="Interview type" name="interview_type" required>
                {interviewTypes.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
              </SelectField>
              <TextField label="Starts at" name="starts_at" required type="datetime-local" />
              <TextField label="Ends at" name="ends_at" required type="datetime-local" />
              <TextField label="Interviewer IDs" name="interviewer_ids" placeholder="INTV-001, INTV-002" required />
              <div className="sm:col-span-2 xl:col-span-3">
                <Button disabled={busy} type="submit">Schedule interview</Button>
              </div>
            </form>
            <p className="mt-3 text-[11px] text-slate-400">The API checks interviewer availability and application eligibility before saving.</p>
          </Panel>
          <Panel title="Interview schedule" action={<span className="text-xs text-slate-400">{rows.length} records</span>}>
            <div className="mb-4 max-w-sm">
              <TextField aria-label="Search interviews" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Interview, application, interviewer…" value={search} />
            </div>
            {loading ? <EmptyState>Loading interviews…</EmptyState> : rows.length === 0 ? <EmptyState>No interviews match this view.</EmptyState> : (
              <Table headers={["Interview", "Application", "Schedule", "Panel", "Status", ""]}>
                {pageSlice(rows, page).map((item) => (
                  <tr className="hover:bg-slate-50/70" key={item.interview_id}>
                    <td className="px-3 py-3 first:pl-0 font-semibold text-slate-800">{item.interview_id}</td>
                    <td className="px-3 py-3 text-slate-600">{item.application_id}</td>
                    <td className="px-3 py-3 text-slate-600">{formatDate(item.starts_at)}</td>
                    <td className="px-3 py-3 text-slate-500">{item.interviewer_ids.join(", ")}</td>
                    <td className="px-3 py-3"><StatusBadge value={item.status} /></td>
                    <td className="px-3 py-3 text-right last:pr-0">
                      <Button onClick={() => selectResource(item.interview_id, `/api/interviews/${encodeURIComponent(item.interview_id)}`)} type="button" variant="secondary">Details</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>
          {selected && (
            <div className="mt-4 space-y-4">
              <DetailCard error={detailError} loading={detailLoading} title={`Interview · ${selected.interview_id}`} value={detail} />
              <Panel title="Reschedule interview">
                <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => {
                  const values = new FormData(event.currentTarget);
                  onSubmit(event, () => api.patch<Interview>(`/api/interviews/${encodeURIComponent(selected.interview_id)}/schedule`, {
                    starts_at: toIsoDateTime(String(values.get("starts_at"))),
                    ends_at: toIsoDateTime(String(values.get("ends_at"))),
                    interviewer_ids: String(values.get("interviewer_ids")).split(",").map((id) => id.trim()).filter(Boolean),
                  }), "Interview schedule updated.");
                }}>
                  <TextField defaultValue={toLocalDateTime(selected.starts_at)} label="Starts at" name="starts_at" required type="datetime-local" />
                  <TextField defaultValue={toLocalDateTime(selected.ends_at)} label="Ends at" name="ends_at" required type="datetime-local" />
                  <TextField defaultValue={selected.interviewer_ids.join(", ")} label="Interviewer IDs" name="interviewer_ids" required />
                  <div className="flex items-end"><Button disabled={busy || selected.status !== "scheduled"} type="submit" variant="secondary">Update schedule</Button></div>
                </form>
              </Panel>
            </div>
          )}
        </>
      );
    }

    if (section === "feedback") {
      const rows = (data.interviews ?? []).filter((item) =>
        matchesText(item.interview_id, item.application_id, item.interviewer_ids.join(" "), item.status),
      );
      const selected = rows.find((item) => item.interview_id === selectedId);
      const feedbackRows = Array.isArray(detail) ? detail as Feedback[] : [];
      return (
        <>
          <PageHeading section={section} />
          {notice && <Notice>{notice}</Notice>}
          {actionError && <Notice error>{actionError}</Notice>}
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel title="Select interview">
            <div className="mb-4 max-w-sm">
              <TextField aria-label="Search feedback interviews" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Interview, application…" value={search} />
            </div>
            {loading ? <EmptyState>Loading interviews…</EmptyState> : rows.length === 0 ? <EmptyState>No interviews are available.</EmptyState> : (
              <Table headers={["Interview", "Application", "Date", "Panel", "Status", ""]}>
                {pageSlice(rows, page).map((item) => (
                  <tr className="hover:bg-slate-50/70" key={item.interview_id}>
                    <td className="px-3 py-3 first:pl-0 font-semibold text-slate-800">{item.interview_id}</td>
                    <td className="px-3 py-3 text-slate-600">{item.application_id}</td>
                    <td className="px-3 py-3 text-slate-500">{formatDate(item.starts_at)}</td>
                    <td className="px-3 py-3 text-slate-500">{item.interviewer_ids.join(", ")}</td>
                    <td className="px-3 py-3"><StatusBadge value={item.status} /></td>
                    <td className="px-3 py-3 text-right last:pr-0">
                      <Button onClick={() => selectResource(item.interview_id, `/api/interviews/${encodeURIComponent(item.interview_id)}/feedback`)} type="button" variant="secondary">Feedback</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>
          {selected && (
            <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_1.25fr]">
              <Panel title={`Submit feedback · ${selected.interview_id}`}>
                <form className="space-y-3" onSubmit={(event) => {
                  const values = new FormData(event.currentTarget);
                  onSubmit(event, () => api.post<Feedback>(`/api/interviews/${encodeURIComponent(selected.interview_id)}/feedback`, {
                    interviewer_id: String(values.get("interviewer_id")),
                    technical_rating: Number(values.get("technical_rating")),
                    communication_rating: Number(values.get("communication_rating")),
                    role_rating: Number(values.get("role_rating")),
                    recommendation: String(values.get("recommendation")),
                    comments: String(values.get("comments") || "") || null,
                  }), "Feedback submitted.");
                }}>
                  <SelectField label="Assigned interviewer" name="interviewer_id" required>
                    {selected.interviewer_ids.map((id) => <option key={id} value={id}>{id}</option>)}
                  </SelectField>
                  <div className="grid grid-cols-3 gap-3">
                    {["technical_rating", "communication_rating", "role_rating"].map((key) => (
                      <SelectField key={key} label={displayLabel(key)} name={key} required>
                        {[1, 2, 3, 4, 5].map((score) => <option key={score} value={score}>{score}</option>)}
                      </SelectField>
                    ))}
                  </div>
                  <SelectField label="Recommendation" name="recommendation" required>
                    {recommendations.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
                  </SelectField>
                  <TextAreaField label="Comments" name="comments" />
                  <Button disabled={busy || !selected.interviewer_ids.length} type="submit">Submit feedback</Button>
                </form>
              </Panel>
              <Panel title="Submitted feedback">
                {detailLoading ? <EmptyState>Loading feedback…</EmptyState> : detailError ? <Notice error>{detailError}</Notice> : feedbackRows.length === 0 ? <EmptyState>No feedback has been submitted for this interview.</EmptyState> : (
                  <Table headers={["Interviewer", "Technical", "Communication", "Role", "Recommendation", "Submitted"]}>
                    {feedbackRows.map((item) => (
                      <tr key={item.feedback_id}>
                        <td className="px-3 py-3 first:pl-0 font-medium text-slate-700">{item.interviewer_id}</td>
                        <td className="px-3 py-3 text-slate-600">{item.technical_rating}/5</td>
                        <td className="px-3 py-3 text-slate-600">{item.communication_rating}/5</td>
                        <td className="px-3 py-3 text-slate-600">{item.role_rating}/5</td>
                        <td className="px-3 py-3"><StatusBadge value={item.recommendation} /></td>
                        <td className="px-3 py-3 last:pr-0 text-slate-500">{formatDate(item.submitted_at)}</td>
                      </tr>
                    ))}
                  </Table>
                )}
              </Panel>
            </div>
          )}
        </>
      );
    }

    if (section === "offers") {
      const rows = (data.offers ?? []).filter((item) =>
        matchesText(item.offer_id, item.application_id, item.status, item.created_by) &&
        (!statusFilter || item.status === statusFilter),
      );
      const selected = rows.find((item) => item.offer_id === selectedId);
      return (
        <>
          <PageHeading section={section} />
          {notice && <Notice>{notice}</Notice>}
          {actionError && <Notice error>{actionError}</Notice>}
          {loadError && <Notice error>{loadError}</Notice>}
          <Panel className="mb-4" title="Create offer">
            <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => {
              const form = event.currentTarget;
              const values = new FormData(event.currentTarget);
              const applicationId = String(values.get("application_id"));
              void onSubmit(event, () => api.post<Offer>(`/api/applications/${encodeURIComponent(applicationId)}/offers`, {
                salary: Number(values.get("salary")),
                created_by: String(values.get("created_by")),
              }), "Offer created.").then((success) => {
                if (success) form.reset();
              });
            }}>
              <TextField label="Application ID" name="application_id" required />
              <TextField label="Salary" min="0.01" name="salary" required step="0.01" type="number" />
              <TextField defaultValue="recruiter" label="Created by" name="created_by" required />
              <div className="flex items-end"><Button disabled={busy} type="submit">Create offer</Button></div>
            </form>
            <p className="mt-3 text-[11px] text-slate-400">Application eligibility and the approved salary band are validated by the API.</p>
          </Panel>
          <Panel title="Offer register" action={<span className="text-xs text-slate-400">{rows.length} records</span>}>
            <div className="mb-4 flex flex-wrap gap-2">
              <TextField aria-label="Search offers" label="Search" onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Offer, application…" value={search} />
              <SelectField label="Status" onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }} value={statusFilter}>
                <option value="">All statuses</option>
                {["pending_approval", "approved", "issued", "accepted", "rejected", "declined", "withdrawn"].map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
              </SelectField>
            </div>
            {loading ? <EmptyState>Loading offers…</EmptyState> : rows.length === 0 ? <EmptyState>No offers match this view.</EmptyState> : (
              <Table headers={["Offer", "Application", "Salary", "Status", "Approved by", ""]}>
                {pageSlice(rows, page).map((item) => (
                  <tr className="hover:bg-slate-50/70" key={item.offer_id}>
                    <td className="px-3 py-3 first:pl-0 font-semibold text-slate-800">{item.offer_id}</td>
                    <td className="px-3 py-3 text-slate-600">{item.application_id}</td>
                    <td className="px-3 py-3 text-slate-600">{formatCurrency(item.salary, item.currency)}</td>
                    <td className="px-3 py-3"><StatusBadge value={item.status} /></td>
                    <td className="px-3 py-3 text-slate-500">{item.approved_by ?? "—"}</td>
                    <td className="px-3 py-3 text-right last:pr-0">
                      <Button onClick={() => selectResource(item.offer_id, `/api/offers/${encodeURIComponent(item.offer_id)}`)} type="button" variant="secondary">Details</Button>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <Pagination onChange={setPage} page={page} total={rows.length} />
          </Panel>
          {selected && (
            <div className="mt-4 space-y-4">
              <DetailCard error={detailError} loading={detailLoading} title={`Offer · ${selected.offer_id}`} value={detail} />
              <Panel title="Offer actions">
                <div className="flex flex-wrap gap-2">
                  {selected.status === "pending_approval" && (
                    <form className="flex w-full flex-wrap items-end gap-3" onSubmit={(event) => {
                      const values = new FormData(event.currentTarget);
                      onSubmit(event, () => api.post(`/api/offers/${encodeURIComponent(selected.offer_id)}/approve`, {
                        actor: String(values.get("actor")),
                        reason: String(values.get("reason") || "") || null,
                      }), "Offer approved.");
                    }}>
                      <TextField defaultValue="recruiter" label="Approver" name="actor" required />
                      <TextField label="Reason" name="reason" />
                      <Button disabled={busy} type="submit">Approve</Button>
                    </form>
                  )}
                  {selected.status === "approved" && (
                    <form className="flex w-full flex-wrap items-end gap-3" onSubmit={(event) => {
                      const values = new FormData(event.currentTarget);
                      onSubmit(event, () => api.post(`/api/offers/${encodeURIComponent(selected.offer_id)}/issue`, {
                        actor: String(values.get("actor")),
                        reason: String(values.get("reason") || "") || null,
                      }), "Offer issued.");
                    }}>
                      <TextField defaultValue="recruiter" label="Actor" name="actor" required />
                      <TextField label="Reason" name="reason" />
                      <Button disabled={busy} type="submit">Issue offer</Button>
                    </form>
                  )}
                  {selected.status === "issued" && (
                    <form className="grid w-full gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(event) => {
                      const values = new FormData(event.currentTarget);
                      onSubmit(event, () => api.post(`/api/offers/${encodeURIComponent(selected.offer_id)}/respond`, {
                        decision: String(values.get("decision")),
                        actor: String(values.get("actor")),
                        reason: String(values.get("reason") || "") || null,
                      }), "Offer response recorded.");
                    }}>
                      <SelectField label="Decision" name="decision" required>
                        <option value="accepted">Accepted</option>
                        <option value="rejected">Rejected</option>
                      </SelectField>
                      <TextField defaultValue="recruiter" label="Actor" name="actor" required />
                      <TextField label="Reason" name="reason" placeholder="Required when rejected" />
                      <div className="flex items-end"><Button disabled={busy} type="submit">Record response</Button></div>
                    </form>
                  )}
                  {!["pending_approval", "approved", "issued"].includes(selected.status) && <span className="text-xs text-slate-500">No further offer action is available.</span>}
                </div>
              </Panel>
            </div>
          )}
        </>
      );
    }

    if (section === "exceptions-audit") {
      const exceptions = (data.exceptions ?? []).filter((item) =>
        matchesText(item.exception_id, item.issue_type, item.entity_type, item.entity_id, item.severity, item.validation_result),
      );
      const audit = (data.audit ?? []).filter((item) =>
        matchesText(item.audit_event_id, item.entity_type, item.entity_id, item.action, item.actor),
      );
      return (
        <>
          <PageHeading section={section} />
          {loadError && <Notice error>{loadError}</Notice>}
          <div className="space-y-4">
            <Panel title="Detected exceptions" action={<span className="text-xs text-slate-400">{exceptions.length} records</span>}>
              <div className="mb-4 max-w-sm">
                <TextField aria-label="Search exceptions and audit" label="Search" onChange={(event) => {
                  setSearch(event.target.value);
                  setExceptionPage(1);
                  setAuditPage(1);
                }} placeholder="Type, entity, severity…" value={search} />
              </div>
              {loading ? <EmptyState>Loading exceptions…</EmptyState> : exceptions.length === 0 ? <EmptyState>No exceptions match this search.</EmptyState> : (
                <Table headers={["Issue", "Entity", "Severity", "Detection", "Detected at", ""]}>
                  {pageSlice(exceptions, exceptionPage).map((item) => (
                    <tr className="hover:bg-slate-50/70" key={item.exception_id}>
                      <td className="px-3 py-3 first:pl-0">
                        <div className="font-medium capitalize text-slate-700">{displayLabel(item.issue_type)}</div>
                        <div className="mt-1 text-[10px] text-slate-400">{item.exception_id}</div>
                      </td>
                      <td className="px-3 py-3 text-slate-600">{displayLabel(item.entity_type)} · {item.entity_id}</td>
                      <td className="px-3 py-3"><StatusBadge value={item.severity} /></td>
                      <td className="px-3 py-3 text-slate-600">{item.detected ? "Detected" : "Not detected"}</td>
                      <td className="px-3 py-3 text-slate-500">{formatDate(item.detected_at)}</td>
                      <td className="px-3 py-3 text-right last:pr-0">
                        <Button onClick={() => selectResource(item.exception_id, `/api/exceptions/${encodeURIComponent(item.exception_id)}`)} type="button" variant="secondary">Details</Button>
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
              <Pagination onChange={setExceptionPage} page={exceptionPage} total={exceptions.length} />
            </Panel>
            <Panel title="Audit history" action={<span className="text-xs text-slate-400">Latest {audit.length} records</span>}>
              {loading ? <EmptyState>Loading audit history…</EmptyState> : audit.length === 0 ? <EmptyState>No audit records match this search.</EmptyState> : (
                <Table headers={["Event", "Entity", "Action", "Actor", "Occurred"]}>
                  {pageSlice(audit, auditPage).map((item) => (
                    <tr className="cursor-pointer hover:bg-slate-50/70" key={item.audit_event_id} onClick={() => selectSnapshot(item.audit_event_id, item)}>
                      <td className="px-3 py-3 first:pl-0 font-medium text-slate-700">{item.audit_event_id}</td>
                      <td className="px-3 py-3 text-slate-600">{displayLabel(item.entity_type)} · {item.entity_id}</td>
                      <td className="px-3 py-3 text-slate-600">{displayLabel(item.action)}</td>
                      <td className="px-3 py-3 text-slate-500">{item.actor}</td>
                      <td className="px-3 py-3 last:pr-0 text-slate-500">{formatDate(item.occurred_at)}</td>
                    </tr>
                  ))}
                </Table>
              )}
              <Pagination onChange={setAuditPage} page={auditPage} total={audit.length} />
            </Panel>
            {selectedId && <DetailCard error={detailError} loading={detailLoading} title="Record details" value={detail} />}
          </div>
        </>
      );
    }

    return null;
  }, [section, data, loading, loadError, search, statusFilter, selectedId, detail, detailLoading, detailError, busy, notice, actionError, relatedApplications, relatedLoading, relatedError, dashboardExceptionError, dashboardExceptionLoading, dashboardUpdatedAt, page, exceptionPage, auditPage, perform, selectResource, selectSnapshot, onSubmit]);

  if (!section) {
    return (
      <AppShell>
        <Panel title="Page not found">
          <p className="text-sm text-slate-500">This page is not available.</p>
        </Panel>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div>
        {content}
        {section !== "dashboard" && notice && section !== "requisitions" && section !== "applications" && section !== "screening" && section !== "interviews" && section !== "feedback" && section !== "offers" && <Notice>{notice}</Notice>}
        {section !== "dashboard" && actionError && section !== "requisitions" && section !== "applications" && section !== "screening" && section !== "interviews" && section !== "feedback" && section !== "offers" && <Notice error>{actionError}</Notice>}
      </div>
    </AppShell>
  );
}

function CurrentSection() {
  const params = useParams<{ section?: string[] }>();
  const section = params.section?.join("/") ?? "dashboard";

  return <Workspace key={section} section={section} />;
}

export default function Page() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-slate-500">Loading workspace…</div>}>
      <CurrentSection />
    </Suspense>
  );
}
