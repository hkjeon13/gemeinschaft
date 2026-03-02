import { createBrowserRouter, redirect } from 'react-router';
import { AdminPage } from './components/AdminPage';
import { ChatPage } from './components/ChatPage';
import { EmailVerificationPage } from './components/EmailVerificationPage';

export const router = createBrowserRouter(
  [
    {
      path: '/chat',
      Component: ChatPage,
    },
    {
      path: '/admin',
      Component: AdminPage,
    },
    {
      path: '/email-verified',
      Component: EmailVerificationPage,
    },
    // /login 과 / 는 /chat 으로 리다이렉트 (로그인은 팝업으로 처리)
    {
      path: '/login',
      loader: () => redirect('/chat'),
    },
    {
      path: '/',
      loader: () => redirect('/chat'),
    },
    {
      path: '*',
      loader: () => redirect('/chat'),
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
