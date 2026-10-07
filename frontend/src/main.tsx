import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router";
import { AuthProvider } from "./auth/AuthContext";
import { FamilyLayout, PlainShell, RequireAuth } from "./components/Layout";
import Account from "./pages/Account";
import AiEntry from "./pages/AiEntry";
import AdminCosts from "./pages/AdminCosts";
import AdminUsers from "./pages/AdminUsers";
import Categories from "./pages/Categories";
import Dashboard from "./pages/Dashboard";
import Families from "./pages/Families";
import FamilySettings from "./pages/FamilySettings";
import { Home, Login, NewPassword } from "./pages/Login";
import NewEntry from "./pages/NewEntry";
import { NotYet } from "./pages/Placeholder";
import Rates from "./pages/Rates";
import Trash from "./pages/Trash";
import "@fontsource-variable/nunito"; // 随站点打包，不从第三方加载字体
import "./styles/prototype.css";
import "./styles/app.css";
import "./styles/theme.css";
import { applyTheme, getTheme } from "./lib/theme";
import { LangKeyed, LangProvider, applyLang, currentLang, tr } from "./lib/i18n";

applyTheme(getTheme()); // 渲染前应用，避免闪现旧风格
applyLang(currentLang());

function NotFound() {
  return <PlainShell title={tr("未找到", "Not found")}><p className="muted">{tr("页面不存在。", "This page does not exist.")}</p></PlainShell>;
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LangKeyed>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/login/new-password" element={<NewPassword />} />
          <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
          <Route path="/families" element={<RequireAuth><Families /></RequireAuth>} />
          <Route path="/account" element={<RequireAuth><Account /></RequireAuth>} />
          <Route path="/admin/users" element={<RequireAuth><AdminUsers /></RequireAuth>} />
          <Route path="/admin/costs" element={<RequireAuth><AdminCosts /></RequireAuth>} />
          <Route path="/f/:fid" element={<RequireAuth><FamilyLayout /></RequireAuth>}>
            <Route index element={<Dashboard />} />
            <Route path="new" element={<NewEntry />} />
            <Route path="ai" element={<AiEntry />} />
            <Route path="trash" element={<Trash />} />
            <Route path="categories" element={<Categories />} />
            <Route path="rates" element={<Rates />} />
            <Route path="settings" element={<FamilySettings />} />
          </Route>
          <Route path="*" element={<RequireAuth><NotFound /></RequireAuth>} />
        </Routes>
        </LangKeyed>
      </AuthProvider>
    </BrowserRouter>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LangProvider>
      <App />
    </LangProvider>
  </StrictMode>,
);
