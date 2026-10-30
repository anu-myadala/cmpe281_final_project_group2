// MUI theme matching the wireframe style guide: Inter, 13 px body, blue #2563eb primary,
// flat white cards on a warm gray content area.
import { createTheme } from '@mui/material/styles';

export const colors = {
  primary: '#2563eb',
  primaryDark: '#1d4ed8',
  primaryLight: '#e8f0fe',
  text: '#1f2328',
  muted: '#555',
  border: '#ddd9d0',
  menuBg: '#f5f4f0',
  contentBg: '#faf9f6',
  destructive: '#b42318',
  banner: '#fff3cd',
  bannerBorder: '#e0c35a',
  bannerText: '#6b5300',
};

export const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626',
  HIGH: '#ea580c',
  MEDIUM: '#ca8a04',
  LOW: '#6b7280',
};

export const CONNECTION_COLORS: Record<string, string> = {
  ONLINE: '#16a34a',
  OFFLINE: '#d97706',
  FAULT: '#dc2626',
  NONE: '#9ca3af',
};

export const CHART_SERIES = ['#2563eb', '#93b4f5', '#1e7b34', '#f6c08a', '#b42318', '#c9c7c0', '#7c3aed'];

export const theme = createTheme({
  palette: {
    primary: { main: colors.primary, dark: colors.primaryDark, light: colors.primaryLight },
    error: { main: colors.destructive },
    background: { default: colors.contentBg, paper: '#fff' },
    text: { primary: colors.text, secondary: colors.muted },
    divider: colors.border,
  },
  typography: {
    fontFamily: 'Inter, "Inter Display", Helvetica, Arial, sans-serif',
    fontSize: 13,
    h6: { fontSize: 17, fontWeight: 600 },
    subtitle1: { fontSize: 13.5, fontWeight: 600 },
    subtitle2: { fontSize: 12, fontWeight: 600 },
    body1: { fontSize: 13 },
    body2: { fontSize: 12 },
    caption: { fontSize: 11.5, color: colors.muted },
    button: { textTransform: 'none', fontSize: 12.5, fontWeight: 600 },
  },
  shape: { borderRadius: 6 },
  components: {
    MuiButton: {
      defaultProps: { size: 'small', disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 5, padding: '4px 10px', lineHeight: 1.4, minWidth: 0 },
        outlined: { borderColor: '#9a9a9a', color: colors.text, '&:hover': { borderColor: '#666', background: '#f7f7f5' } },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: { root: { border: `1px solid ${colors.border}`, borderRadius: 6 } },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { fontSize: 12, padding: '3px 6px', borderBottom: '1px solid #e3e0d8', whiteSpace: 'nowrap' },
        head: { background: '#f5f4f0', fontWeight: 600, color: '#444', position: 'sticky', top: 0, zIndex: 1 },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { height: 18, fontSize: 11, borderRadius: 10, fontWeight: 500 },
        label: { paddingLeft: 7, paddingRight: 7 },
      },
    },
    MuiTextField: { defaultProps: { size: 'small' } },
    MuiSelect: { defaultProps: { size: 'small' } },
    MuiFormControl: { defaultProps: { size: 'small' } },
    MuiInputBase: { styleOverrides: { root: { fontSize: 12.5, background: '#fff' } } },
    MuiInputLabel: { styleOverrides: { root: { fontSize: 12.5 } } },
    MuiMenuItem: { styleOverrides: { root: { fontSize: 12.5 } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 8 } } },
    MuiDialogTitle: { styleOverrides: { root: { fontSize: 14, fontWeight: 600, padding: '12px 16px 4px' } } },
    MuiDialogContent: { styleOverrides: { root: { padding: '8px 16px', fontSize: 12.5 } } },
    MuiTooltip: { styleOverrides: { tooltip: { fontSize: 11.5 } } },
  },
});
