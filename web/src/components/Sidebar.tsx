import React from "react";
import type { User } from "../types";

interface SidebarProps {
  user: User;
  currentPage: string;
  onNavigate: (page: string) => void;
  onLogout: () => void;
  pendingApprovals?: number;
}

interface NavItem {
  id: string;
  icon: string;
  label: string;
  badge?: number;
}

export function Sidebar({ user, currentPage, onNavigate, onLogout, pendingApprovals }: SidebarProps) {
  const navItems: NavItem[] = [
    { id: "dashboard", icon: "🏠", label: "概览" },
    { id: "records", icon: "📚", label: "所有记录" },
    { id: "approvals", icon: "✅", label: "等待确认", badge: pendingApprovals },
    { id: "settings", icon: "⚙️", label: "运行控制" },
  ];

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <h1>漫画管线</h1>
        <span>Manga Pipeline Console</span>
      </div>

      {navItems.map(item => (
        <button
          key={item.id}
          type="button"
          className={`nav-item${currentPage === item.id ? " active" : ""}`}
          onClick={() => onNavigate(item.id)}
        >
          <span className="nav-icon">{item.icon}</span>
          {item.label}
          {item.badge != null && item.badge > 0 && (
            <span className="nav-badge">{item.badge}</span>
          )}
        </button>
      ))}

      <div className="sidebar-footer">
        <div className="sidebar-user">
          <strong>{user.username}</strong>
          已登录
        </div>
        <button type="button" className="btn btn-secondary btn-sm" style={{ width: "100%", justifyContent: "center" }} onClick={onLogout}>
          退出登录
        </button>
      </div>
    </nav>
  );
}
