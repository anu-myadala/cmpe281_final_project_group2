// ChartCard: Recharts wrappers (stacked bars, simple bars, donut) in a Card.
import type { ReactNode } from 'react';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from './common';
import { CHART_SERIES, colors } from '../theme';

export function ChartCard({ title, note, actions, height = 200, children, sx, empty }: { title: ReactNode; note?: ReactNode; actions?: ReactNode; height?: number; children: ReactNode; sx?: Record<string, unknown>; empty?: boolean }) {
  return (
    <Card title={title} note={note} actions={actions} sx={sx} testId="chart-card">
      <Box sx={{ height, position: 'relative' }}>
        {empty ? (
          <Box sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Typography variant="caption">No data in this range</Typography>
          </Box>
        ) : (
          children
        )}
      </Box>
    </Card>
  );
}

const axisStyle = { fontSize: 10, fill: colors.muted };

export function StackedBars({ data, keys, xKey, colorsByKey }: { data: Array<Record<string, number | string>>; keys: string[]; xKey: string; colorsByKey?: Record<string, string> }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e3e0d8" vertical={false} />
        <XAxis dataKey={xKey} tick={axisStyle} tickLine={false} axisLine={{ stroke: '#999' }} />
        <YAxis tick={axisStyle} tickLine={false} axisLine={false} allowDecimals={false} />
        <Tooltip contentStyle={{ fontSize: 11.5 }} cursor={{ fill: 'rgba(37,99,235,.06)' }} />
        <Legend wrapperStyle={{ fontSize: 11 }} iconSize={9} />
        {keys.map((key, index) => (
          <Bar key={key} dataKey={key} stackId="a" fill={colorsByKey?.[key] ?? CHART_SERIES[index % CHART_SERIES.length]} maxBarSize={36} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function SimpleBars({ data, valueKey, xKey, color = CHART_SERIES[0] }: { data: Array<Record<string, number | string>>; valueKey: string; xKey: string; color?: string }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e3e0d8" vertical={false} />
        <XAxis dataKey={xKey} tick={axisStyle} tickLine={false} axisLine={{ stroke: '#999' }} interval={0} />
        <YAxis tick={axisStyle} tickLine={false} axisLine={false} allowDecimals={false} />
        <Tooltip contentStyle={{ fontSize: 11.5 }} cursor={{ fill: 'rgba(37,99,235,.06)' }} />
        <Bar dataKey={valueKey} fill={color} maxBarSize={40} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Donut({ data, colorsByName }: { data: Array<{ name: string; value: number }>; colorsByName?: Record<string, string> }) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie data={total > 0 ? data : [{ name: 'none', value: 1 }]} dataKey="value" nameKey="name" innerRadius="52%" outerRadius="85%" paddingAngle={1} stroke="#fff" isAnimationActive={false}>
          {(total > 0 ? data : [{ name: 'none', value: 1 }]).map((entry, index) => (
            <Cell key={entry.name} fill={total > 0 ? colorsByName?.[entry.name] ?? CHART_SERIES[index % CHART_SERIES.length] : '#e5e5e5'} />
          ))}
        </Pie>
        {total > 0 && <Tooltip contentStyle={{ fontSize: 11.5 }} />}
        <Legend layout="vertical" align="right" verticalAlign="middle" wrapperStyle={{ fontSize: 11 }} iconSize={9} formatter={(value: string) => `${value} ${data.find((d) => d.name === value)?.value ?? ''}`} />
      </PieChart>
    </ResponsiveContainer>
  );
}
