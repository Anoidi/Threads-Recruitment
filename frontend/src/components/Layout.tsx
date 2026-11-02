import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, AtSign, BarChart3, Bot, BriefcaseBusiness, CalendarDays, FileImage, FileText, LayoutDashboard, Menu, MessageCircleMore, MessagesSquare, Settings, UserRoundSearch, X } from "lucide-react";
import { isMockMode } from "../api/client";

const nav = [
  ["Главная", "/", LayoutDashboard], ["Контент", "/content", FileText], ["Календарь", "/calendar", CalendarDays], ["Медиа", "/media", FileImage],
  ["Сообщество", "/community", MessageCircleMore], ["Comment Engagement", "/comment-engagement", MessagesSquare], ["Кандидаты", "/candidates", UserRoundSearch], ["Вакансии", "/vacancies", BriefcaseBusiness],
  ["Аналитика", "/analytics", BarChart3], ["ИИ-агенты", "/agents", Bot], ["Аккаунт Threads", "/account", AtSign], ["Настройки", "/settings", Settings],
] as const;

export function Layout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const current = nav.find(([, path]) => path === location.pathname)?.[0] ?? "Рабочая область";
  return <div className="min-h-screen bg-canvas">
    <aside className={`fixed inset-y-0 left-0 z-40 w-64 border-r border-slate-200 bg-[#18211b] text-white transition-transform lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
      <div className="flex h-16 items-center justify-between border-b border-white/10 px-5"><div className="flex items-center gap-3"><div className="grid h-9 w-9 place-items-center rounded-lg bg-moss-500 font-black">TR</div><div><div className="text-sm font-bold leading-tight">Threads Recruitment</div><div className="text-xs text-slate-400">Менеджер</div></div></div><button className="p-1 lg:hidden" onClick={() => setOpen(false)} aria-label="Закрыть меню"><X /></button></div>
      <nav className="space-y-1 p-3" aria-label="Основная навигация">{nav.map(([label, path, Icon]) => <NavLink key={path} to={path} end={path === "/"} onClick={() => setOpen(false)} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${isActive ? "bg-white/12 text-white" : "text-slate-300 hover:bg-white/7 hover:text-white"}`}><Icon className="h-[18px] w-[18px]" />{label}</NavLink>)}</nav>
      <div className="absolute inset-x-3 bottom-3 rounded-lg border border-white/10 bg-white/5 p-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-300"><Activity className="h-4 w-4 text-emerald-400" />Защитные ограничения включены</div><p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">Внешние действия выполняются согласно настройкам безопасности.</p></div>
    </aside>
    {open && <button aria-label="Закрыть навигацию" className="fixed inset-0 z-30 bg-black/30 lg:hidden" onClick={() => setOpen(false)} />}
    <div className="lg:pl-64">
      <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6"><div className="flex items-center gap-3"><button className="rounded-lg p-2 hover:bg-slate-100 lg:hidden" onClick={() => setOpen(true)} aria-label="Открыть меню"><Menu className="h-5 w-5" /></button><span className="text-sm font-semibold text-slate-700">{current}</span></div><div className="flex items-center gap-3"><span className="hidden text-xs text-slate-500 sm:block">Europe/Warsaw</span><div className="grid h-8 w-8 place-items-center rounded-full bg-clay-50 text-xs font-bold text-clay-600">AR</div></div></header>
      {isMockMode && <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-xs font-medium text-amber-800"><strong>Тестовый режим</strong> — имитация данных Threads и ИИ изолирована от рабочей среды.</div>}
      <main className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-7"><Outlet /></main>
    </div>
  </div>;
}
