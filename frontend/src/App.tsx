import { Navigate, Route, Routes } from "react-router-dom";
import { CommentEngagementPage } from "./pages/CommentEngagementPage";
import { Layout } from "./components/Layout";
import { OperatorGate } from "./components/OperatorGate";
import { DashboardPage } from "./pages/DashboardPage";
import { ContentPage } from "./pages/ContentPage";
import { CalendarPage } from "./pages/CalendarPage";
import { CommunityPage } from "./pages/CommunityPage";
import { CandidatesPage } from "./pages/CandidatesPage";
import { VacanciesPage } from "./pages/VacanciesPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { AgentsPage } from "./pages/AgentsPage";
import { AccountPage } from "./pages/AccountPage";
import { SettingsPage } from "./pages/SettingsPage";
import { MediaPage } from "./pages/MediaPage";

export function App() {
  return <OperatorGate><Routes><Route element={<Layout />}><Route index element={<DashboardPage />} /><Route path="content" element={<ContentPage />} /><Route path="calendar" element={<CalendarPage />} /><Route path="media" element={<MediaPage />} /><Route path="community" element={<CommunityPage />} /><Route path="comment-engagement" element={<CommentEngagementPage />} /><Route path="candidates" element={<CandidatesPage />} /><Route path="vacancies" element={<VacanciesPage />} /><Route path="analytics" element={<AnalyticsPage />} /><Route path="agents" element={<AgentsPage />} /><Route path="account" element={<AccountPage />} /><Route path="settings" element={<SettingsPage />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes></OperatorGate>;
}
