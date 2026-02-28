import { AdminPage } from "./pages/AdminPage";
import { ConsolePage } from "./pages/ConsolePage";

export default function App(): JSX.Element {
  const path = window.location.pathname;
  if (path === "/admin" || path === "/admin/") {
    return <AdminPage />;
  }

  return <ConsolePage />;
}
