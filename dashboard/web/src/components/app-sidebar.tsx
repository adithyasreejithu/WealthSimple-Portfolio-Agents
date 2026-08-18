"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  CandlestickChart,
  LayoutDashboard,
  Layers,
  PiggyBank,
  Radar,
  ShieldAlert,
  Star,
} from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

const NAV = [
  { title: "Overview", href: "/", icon: LayoutDashboard },
  { title: "Portfolio", href: "/portfolio", icon: BarChart3 },
  { title: "Stocks", href: "/stocks", icon: CandlestickChart },
  { title: "ETFs", href: "/etfs", icon: Layers },
  { title: "Risk", href: "/risk", icon: Radar },
  { title: "Income", href: "/income", icon: PiggyBank },
  { title: "Wishlist", href: "/wishlist", icon: Star },
  { title: "Data Quality", href: "/data-quality", icon: ShieldAlert },
];

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <BarChart3 className="size-4" />
          </div>
          <div className="grid text-sm leading-tight group-data-[collapsible=icon]:hidden">
            <span className="font-semibold">Portfolio</span>
            <span className="text-muted-foreground text-xs">Dashboard</span>
          </div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV.map((item) => {
                const active =
                  item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={active}
                      tooltip={item.title}
                      render={
                        <Link href={item.href}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      }
                    />
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}
