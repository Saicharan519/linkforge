import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { RootLayout } from "@/components/layout/RootLayout";
import Dashboard from "@/pages/Dashboard";
import Landing from "@/pages/Landing";
import LinkAnalytics from "@/pages/LinkAnalytics";
import Links from "@/pages/Links";
import Login from "@/pages/Login";
import NotFound from "@/pages/NotFound";
import Register from "@/pages/Register";
import Settings from "@/pages/Settings";

// Exported as data so tests build the very same tree with a memory router.
export const routes: RouteObject[] = [
  {
    element: <RootLayout />,
    children: [
      { path: "/", element: <Landing /> },
      { path: "/login", element: <Login /> },
      { path: "/register", element: <Register /> },
      {
        path: "/app",
        element: <ProtectedRoute />,
        children: [
          {
            element: <AppShell />,
            children: [
              { index: true, element: <Dashboard /> },
              { path: "links", element: <Links /> },
              { path: "links/:id", element: <LinkAnalytics /> },
              { path: "settings", element: <Settings /> },
            ],
          },
        ],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
];

export const createAppRouter = () => createBrowserRouter(routes);
