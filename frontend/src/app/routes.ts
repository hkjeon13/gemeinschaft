import { createBrowserRouter } from 'react-router';
import { LoginPage } from './components/LoginPage';
import { AdminPage } from './components/AdminPage';
import { ChatPage } from './components/ChatPage';

export const router = createBrowserRouter(
  [
    {
      path: '/login',
      Component: LoginPage,
    },
    {
      path: '/admin',
      Component: AdminPage,
    },
    {
      path: '/chat',
      Component: ChatPage,
    },
    {
      path: '/',
      loader: () => {
        // 루트 경로는 로그인 페이지로 리다이렉트
        window.location.href = '/login';
        return null;
      },
    },
    {
      path: '*',
      Component: () => {
        window.location.href = '/login';
        return null;
      },
    },
  ],
  {
    future: {
      v7_startTransition: true,
      v7_relativeSplatPath: true,
      v7_fetcherPersist: true,
      v7_normalizeFormMethod: true,
      v7_partialHydration: true,
      v7_skipActionErrorRevalidation: true,
    },
  }
);