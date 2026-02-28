import { createBrowserRouter } from 'react-router';
import { LoginPage } from './components/LoginPage';
import { AdminPage } from './components/AdminPage';

export const router = createBrowserRouter([
  {
    path: '/login',
    Component: LoginPage,
  },
  {
    path: '/admin',
    Component: AdminPage,
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
]);
