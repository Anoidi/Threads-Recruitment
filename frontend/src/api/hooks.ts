import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AgentConfig, CandidateSignal, CandidateStage, ContactChannel, GeneratePostInput, MediaGenerationInput, Post, PostInput, Settings, SocialAccount, Vacancy, VacancyInput } from "../types";

export const keys = {
  health: ["health"] as const, dashboard: ["dashboard"] as const, vacancies: ["vacancies"] as const,
  posts: ["posts"] as const, community: ["community"] as const, commentEngagement: ["comment-engagement"] as const, signals: ["signals"] as const,
  candidates: ["candidates"] as const, candidateFunnel: ["candidate-funnel"] as const,
  agents: ["agents"] as const, account: ["account"] as const, socialAccounts: ["social-accounts"] as const, mediaStatus: ["media-status"] as const, mediaGenerations: ["media-generations"] as const, settings: ["settings"] as const, analytics: ["analytics"] as const,
};

export const useHealth = () => useQuery({ queryKey: keys.health, queryFn: api.health, refetchInterval: 30_000 });
export const useDashboard = () => useQuery({ queryKey: keys.dashboard, queryFn: api.dashboard, refetchInterval: 30_000 });
export const useVacancies = () => useQuery({ queryKey: keys.vacancies, queryFn: api.vacancies });
export const usePosts = () => useQuery({
  queryKey: keys.posts,
  queryFn: api.posts,
  // Publishing is deliberately asynchronous: the API queues a job and the
  // worker talks to Threads. Keep the content list current until that job
  // reaches a terminal state, so the UI does not look stuck on APPROVED.
  refetchInterval: query => query.state.data?.some(post => post.status === "PUBLISHING") ? 1_000 : false,
});
export const useCommunity = () => useQuery({ queryKey: keys.community, queryFn: api.community });
export const useCommentEngagement = () => useQuery({ queryKey: keys.commentEngagement, queryFn: api.commentEngagement });
export const useSignals = () => useQuery({ queryKey: keys.signals, queryFn: api.signals });
export const useAgents = () => useQuery({
  queryKey: keys.agents,
  queryFn: api.agents,
  refetchInterval: query => query.state.data?.some(agent => agent.status === "QUEUED" || agent.status === "RUNNING") ? 1_000 : false,
});
export const useAccount = () => useQuery({ queryKey: keys.account, queryFn: api.account });
export const useSocialAccounts = () => useQuery({ queryKey: keys.socialAccounts, queryFn: api.socialAccounts });
export const useMediaStatus = () => useQuery({ queryKey: keys.mediaStatus, queryFn: api.mediaStatus, refetchInterval: 30_000 });
export const useMediaGenerations = () => useQuery({ queryKey: keys.mediaGenerations, queryFn: api.mediaGenerations, refetchInterval: query => query.state.data?.some(item => item.status === "QUEUED" || item.status === "RUNNING") ? 1_000 : false });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings });
export const useAnalytics = () => useQuery({ queryKey: keys.analytics, queryFn: api.analytics });

function useInvalidate(...queryKeys: readonly (readonly string[])[]) {
  const client = useQueryClient();
  return () => Promise.all(queryKeys.map(queryKey => client.invalidateQueries({ queryKey })));
}

export function useCreateVacancy() { const invalidate = useInvalidate(keys.vacancies, keys.dashboard); return useMutation({ mutationFn: (value: VacancyInput) => api.createVacancy(value), onSuccess: invalidate }); }
export function useUpdateVacancy() { const invalidate = useInvalidate(keys.vacancies); return useMutation({ mutationFn: ({ id, value }: { id: string; value: Partial<Vacancy> }) => api.updateVacancy(id, value), onSuccess: invalidate }); }
export function useCreatePost() { const invalidate = useInvalidate(keys.posts, keys.dashboard); return useMutation({ mutationFn: (value: PostInput) => api.createPost(value), onSuccess: invalidate }); }
export function useGeneratePost() { const invalidate = useInvalidate(keys.posts, keys.dashboard); return useMutation({ mutationFn: (value: GeneratePostInput) => api.generatePost(value), onSuccess: invalidate }); }
export function useGenerateProvocative() { const invalidate = useInvalidate(keys.posts, keys.dashboard); return useMutation({ mutationFn: ({ vacancyId, cta }: { vacancyId: string; cta?: string }) => api.generateProvocative(vacancyId, cta), onSuccess: invalidate }); }
export function useUpdatePost() { const invalidate = useInvalidate(keys.posts); const client = useQueryClient(); return useMutation({ mutationFn: ({ id, value }: { id: string; value: Partial<Post> }) => api.updatePost(id, value), onSuccess: () => { invalidate(); client.invalidateQueries({ queryKey: ['editorial-memory'] }); } }); }
export function usePostAction() { const invalidate = useInvalidate(keys.posts, keys.dashboard); return useMutation({ mutationFn: ({ id, action, scheduledAt, version, note }: { id: string; action: string; scheduledAt?: string; version?: number; note?: string }) => api.transitionPost(id, action, scheduledAt, version, note), onSuccess: invalidate }); }
export function useCommunityAction() { const invalidate = useInvalidate(keys.community, keys.dashboard); return useMutation({ mutationFn: ({ id, action, reply }: { id: string; action: string; reply?: string }) => api.communityAction(id, action, reply), onSuccess: invalidate }); }
export function useSendCommunityReply() { const invalidate = useInvalidate(keys.community, keys.dashboard); return useMutation({ mutationFn: api.sendCommunityReply, onSuccess: invalidate }); }
export function useRejectCommunityReply() { const invalidate = useInvalidate(keys.community, keys.dashboard); return useMutation({ mutationFn: api.rejectCommunityReply, onSuccess: invalidate }); }
export function useMarkCandidate() { const invalidate = useInvalidate(keys.community, keys.signals, keys.dashboard); return useMutation({ mutationFn: api.markCandidate, onSuccess: invalidate }); }
export function useGenerateCommentEngagement() { const invalidate = useInvalidate(keys.commentEngagement); return useMutation({ mutationFn: ({ interaction_id, mode }: { interaction_id: number; mode?: "MONITOR" | "DRAFT" | "AUTO" }) => api.generateCommentEngagement(interaction_id, mode), onSuccess: invalidate }); }
export function useApproveCommentEngagement() { const invalidate = useInvalidate(keys.commentEngagement, keys.posts); return useMutation({ mutationFn: ({ campaign_id, variant_id, content }: { campaign_id: number; variant_id: number; content?: string }) => api.approveCommentEngagement(campaign_id, variant_id, content), onSuccess: invalidate }); }
export function useUpdateSignal() { const invalidate = useInvalidate(keys.signals); return useMutation({ mutationFn: ({ id, status }: { id: string; status: CandidateSignal["status"] }) => api.updateSignal(id, status), onSuccess: invalidate }); }
export function useConvertSignal() { const invalidate = useInvalidate(keys.signals, keys.candidates, keys.dashboard); return useMutation({ mutationFn: ({ id, payload }: { id: string; payload: { channel: ContactChannel; contact_value: string; consent_given?: boolean; notes?: string } }) => api.convertSignal(id, payload), onSuccess: invalidate }); }
export const useCandidates = () => useQuery({ queryKey: keys.candidates, queryFn: api.candidates });
export const useCandidateFunnel = () => useQuery({ queryKey: keys.candidateFunnel, queryFn: api.candidateFunnel });
export function useChangeCandidateStage() { const invalidate = useInvalidate(keys.candidates, keys.candidateFunnel, keys.dashboard); return useMutation({ mutationFn: ({ id, stage, reason }: { id: string; stage: CandidateStage; reason?: string }) => api.changeCandidateStage(id, stage, reason), onSuccess: invalidate }); }
export function useAgentToggle() { const invalidate = useInvalidate(keys.agents); return useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api.toggleAgent(id, enabled), onSuccess: invalidate }); }
export function useAgentRun() {
  const client = useQueryClient();
  const invalidate = useInvalidate(keys.agents, keys.dashboard);
  return useMutation({
    mutationFn: api.runAgent,
    onSuccess: async agent => {
      client.setQueryData<AgentConfig[]>(keys.agents, current => current?.map(item => item.id === agent.id ? agent : item));
      await invalidate();
    },
  });
}
export function useContentPlan() { const invalidate = useInvalidate(keys.posts, keys.dashboard); return useMutation({ mutationFn: api.contentPlan, onSuccess: invalidate }); }
export function useAccountAction() { const invalidate = useInvalidate(keys.account, keys.health); return useMutation({ mutationFn: api.accountAction, onSuccess: invalidate }); }
export function useSocialAction() { const invalidate = useInvalidate(keys.socialAccounts); return useMutation({ mutationFn: ({ platform, action }: { platform: SocialAccount["platform"]; action: "connect" | "test" | "disconnect" }) => api.socialAction(platform, action), onSuccess: invalidate }); }
export function useCreateMediaGeneration() { const invalidate = useInvalidate(keys.mediaGenerations); return useMutation({ mutationFn: (value: MediaGenerationInput) => api.createMediaGeneration(value), onSuccess: invalidate }); }
export function useRetryMediaGeneration() { const invalidate = useInvalidate(keys.mediaGenerations); return useMutation({ mutationFn: api.retryMediaGeneration, onSuccess: invalidate }); }
export function useSaveSettings() { const invalidate = useInvalidate(keys.settings); return useMutation({ mutationFn: (value: Settings) => api.saveSettings(value), onSuccess: invalidate }); }
