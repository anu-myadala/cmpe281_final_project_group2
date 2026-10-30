// App: providers (theme, QueryCache, auth, toasts) around the RoleRouter.
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import { queryCache } from './api/queries';
import { AuthProvider } from './auth/AuthProvider';
import { ToastProvider } from './components/Toast';
import { RoleRouter } from './shell/RoleRouter';
import { theme } from './theme';

export function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryCache}>
        <ToastProvider>
          <AuthProvider>
            <BrowserRouter>
              <RoleRouter />
            </BrowserRouter>
          </AuthProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
