"""Orthographic XY editing canvas; all interactions use stage micrometres."""
import math
import tkinter as tk
from types import SimpleNamespace

from .mapping_region import region_geometry

COLORS = {0:'#526985', 1:'#34d399', 2:'#fbbf24', 3:'#fb7185'}


class MappingCanvas(tk.Canvas):
    def __init__(self, parent, panel):
        super().__init__(parent, background='#101c2c', highlightthickness=0, width=520, height=400)
        self.panel = panel
        self.bounds = (-20, -20, 120, 120)
        self.vertices = []
        self.current = None
        self.stage_position = (None, None)
        self.selected_target = None
        self.tool_mode = 'cursor'
        self.editing = False
        self._drag = None
        self.bind('<Configure>', lambda e:self.draw())
        self.bind('<Button-1>', self.click)
        self.bind('<B1-Motion>', self.drag)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Double-1>', self.double_click)
        self.bind('<Motion>', self.motion)
        self.bind('<Return>', lambda e:self.commit())
        self.bind('<Escape>', lambda e:self.cancel())
        self.bind('<BackSpace>', lambda e:self.undo())
        self.bind('<MouseWheel>', self.zoom)
        self.bind('<ButtonPress-2>', self.pan_start)
        self.bind('<B2-Motion>', self.pan)
        self.bind('<ButtonPress-3>', self.pan_start)
        self.bind('<B3-Motion>', self.pan)

    def transform(self):
        x0,y0,x1,y1 = self.bounds
        scale = min(max(1,self.winfo_width()-120)/(x1-x0), max(1,self.winfo_height()-120)/(y1-y0))
        return scale, (self.winfo_width()-scale*(x1-x0))/2, (self.winfo_height()-scale*(y1-y0))/2

    def screen(self,x,y):
        scale,ox,oy = self.transform()
        return ox+(x-self.bounds[0])*scale, self.winfo_height()-oy-(y-self.bounds[1])*scale

    def world(self,event):
        scale,ox,oy = self.transform()
        return self.bounds[0]+(event.x-ox)/scale, self.bounds[1]+(self.winfo_height()-oy-event.y)/scale

    def snapped(self,event):
        x,y = self.world(event)
        if self.panel.snap_var.get():
            try:
                dx=float(self.panel.variables['step_x_um'].get())
                dy=float(self.panel.variables['step_y_um'].get())
                if dx<=0 or dy<=0:
                    return x,y
                sx,sy=round(x/dx)*dx,round(y/dy)*dy
                scale,_,_=self.transform()
                if math.hypot(x-sx,y-sy)*scale<=12:
                    x,y=sx,sy
                if self.vertices and math.dist((x,y),self.vertices[0])*scale<=12:
                    x,y=self.vertices[0]
            except (ValueError,ZeroDivisionError):
                pass
        return x,y

    def fit(self):
        if self.panel.plan:
            s=self.panel.plan.settings
            x0,x1=sorted((s.x_a_um,s.x_b_um)); y0,y1=sorted((s.y_a_um,s.y_b_um))
            dx=max(x1-x0,s.step_x_um); dy=max(y1-y0,s.step_y_um)
            self.bounds=(x0-dx*.15,y0-dy*.15,x1+dx*.15,y1+dy*.15)
        self.draw()

    def begin(self):
        if self.panel.running:
            return
        self.set_tool_mode('cursor')
        self.vertices=[]
        self.editing=True
        self._drag=None
        self.focus_set()
        self.draw()
        self.panel.show_draw_hint(
            self.panel.region_editor.hint_var.get()
            + "  ·  Backspace undo  ·  Esc cancel"
        )

    def cancel(self, *, redraw=True):
        if self._drag and self._drag['kind']!='pan':
            self.panel.region_editor.set_points(self._drag['points'])
        self._drag=None
        self.editing=False
        self.vertices=[]
        self.panel.hide_draw_hint()
        if redraw:
            self.draw()

    def undo(self):
        if self.editing and self.vertices:
            self.vertices.pop()
            self.draw()

    def required_points(self):
        editor=self.panel.region_editor
        return 3 if editor.method_var.get()=='three_points' else 2

    def click(self,event):
        self.focus_set()
        x,y=self.snapped(event)
        editor=self.panel.region_editor
        if self.editing and not self.panel.running:
            if editor.shape_var.get()=='polygon' and len(self.vertices)>=3 and (x,y)==self.vertices[0]:
                self.commit()
                return
            self.vertices.append((x,y))
            self.draw()
            if editor.shape_var.get()!='polygon' and len(self.vertices)==self.required_points():
                self.commit()
            return
        if self.tool_mode == 'hand':
            self._drag={'kind':'pan','moved':False}
            self.pan_start(event)
            return
        try:
            points=editor.points()
        except ValueError:
            points=[]
        if not self.panel.running:
            for index,point in enumerate(points):
                if math.dist(self.screen(*point),(event.x,event.y))<=10:
                    self._drag={'kind':'handle','index':index,'points':points,'origin':(x,y),'moved':False}
                    return
            if event.state & 0x0001 and points:
                self._drag={'kind':'move','points':points,'origin':(x,y),'moved':False}
                return
        self._drag=None
        if self.panel.plan:
            point=min(self.panel.plan.points,key=lambda p:(p.stage_x_um-x)**2+(p.stage_y_um-y)**2)
            if math.dist(self.screen(point.stage_x_um,point.stage_y_um),(event.x,event.y))<12:
                result=self.panel.results.get((point.row,point.col))
                self.panel.detail.set(f'{point.name}\nX={point.stage_x_um:g}, Y={point.stage_y_um:g} µm\n{result.status if result else "pending"}')

    def double_click(self,event):
        if self.tool_mode != 'cursor' or self.editing or self.panel.running:
            return 'break'
        x,y=self.snapped(event)
        self.selected_target=(x,y)
        self.draw()
        if bool(getattr(event,'state',0) & 0x0001):
            self.panel.move_to_canvas_position(x,y)
        return 'break'

    def set_tool_mode(self, mode):
        if mode not in ('hand','cursor'):
            raise ValueError(f'Unknown canvas tool: {mode}')
        if mode == 'hand' and self.editing:
            self.cancel(redraw=False)
        self.tool_mode=mode
        self.configure(cursor='fleur' if mode=='hand' else 'crosshair')
        if hasattr(self.panel,'set_canvas_tool'):
            self.panel.set_canvas_tool(mode, update_canvas=False)
        self.draw()

    def drag(self,event):
        if not self._drag:
            return
        if self._drag['kind']=='pan':
            self.pan(event)
            return
        if self.panel.running:
            return
        self._drag['moved']=True
        x,y=self.snapped(event)
        points=list(self._drag['points'])
        if self._drag['kind']=='handle':
            points[self._drag['index']]=(x,y)
        else:
            ax,ay=self._drag['origin']
            points=[(px+x-ax,py+y-ay) for px,py in points]
        self.panel.region_editor.set_points(points)
        self.draw()

    def release(self,event):
        if not self._drag:
            return
        drag=self._drag
        self._drag=None
        if drag['kind']!='pan' and drag['moved'] and not self.panel.running:
            self.panel.preview(fit=False)

    def commit(self):
        if not self.editing or self.panel.running:
            return
        editor=self.panel.region_editor
        shape,method=editor.shape_var.get(),editor.method_var.get()
        try:
            region_geometry(shape,method,self.vertices)
        except ValueError as exc:
            self.panel.status.set(str(exc)+' · Backspace to correct the last point.')
            return
        editor.set_points(self.vertices)
        self.editing=False
        self.vertices=[]
        self.panel.hide_draw_hint()
        self.panel.preview(fit=False)

    def motion(self,event):
        x,y=self.snapped(event)
        self.delete('cursor')
        self.create_text(14,self.winfo_height()-14,text=f'X {x:.3f}   Y {y:.3f} µm',anchor='w',fill='#b7cbe5',tags='cursor')
        if self.tool_mode == 'cursor':
            sx,sy=self.screen(x,y)
            color='#fbbf24' if self.editing else '#67e8f9'
            self.create_line(sx-9,sy,sx+9,sy,fill=color,tags='cursor')
            self.create_line(sx,sy-9,sx,sy+9,fill=color,tags='cursor')
        if self.editing:
            self.outline(self.vertices+[(x,y)],tag='cursor',color='#fbbf24',draft=True)

    def zoom(self,event):
        x,y=self.world(event)
        factor=.8 if event.delta>0 else 1.25
        span=self.bounds[2]-self.bounds[0]
        if not 1e-6<span*factor<1e12:
            return
        self.bounds=tuple(center+(value-center)*factor for value,center in zip(self.bounds,(x,y,x,y)))
        self.draw()

    def pan_start(self,event):
        self._pan=(event.x,event.y,self.bounds)

    def pan(self,event):
        if not hasattr(self,'_pan'):
            return
        x,y,bounds=self._pan
        scale,_,_=self.transform()
        dx,dy=(event.x-x)/scale,(event.y-y)/scale
        self.bounds=(bounds[0]-dx,bounds[1]+dy,bounds[2]-dx,bounds[3]+dy)
        self.draw()

    def draw_axes(self):
        width,height=self.winfo_width(),self.winfo_height()
        if width<100 or height<100:
            return
        scale,_,_=self.transform()
        raw=90/scale
        power=10**math.floor(math.log10(raw))
        step=next(value*power for value in (1,2,5,10) if value*power>=raw)
        minor_step=step/5
        xmin,ymax=self.world(SimpleNamespace(x=45,y=55))
        xmax,ymin=self.world(SimpleNamespace(x=width-20,y=height-40))
        for i in range(math.ceil(xmin/minor_step),math.floor(xmax/minor_step)+1):
            value=i*minor_step
            x,_=self.screen(value,0)
            self.create_line(x,55,x,height-40,fill='#18283c',tags=('axes','minor_grid'))
        for i in range(math.ceil(ymin/minor_step),math.floor(ymax/minor_step)+1):
            value=i*minor_step
            _,y=self.screen(0,value)
            self.create_line(45,y,width-20,y,fill='#18283c',tags=('axes','minor_grid'))
        for i in range(math.ceil(xmin/step),math.floor(xmax/step)+1):
            value=i*step
            x,_=self.screen(value,0)
            self.create_line(x,55,x,height-40,fill='#2d4563',width=1.2,tags=('axes','major_grid'))
            self.create_text(x,height-28,text=f'{value:g}',fill='#8ea9cb',font=('Segoe UI',8),tags='axes')
        for i in range(math.ceil(ymin/step),math.floor(ymax/step)+1):
            value=i*step
            _,y=self.screen(0,value)
            self.create_line(45,y,width-20,y,fill='#2d4563',width=1.2,tags=('axes','major_grid'))
            self.create_text(40,y,text=f'{value:g}',anchor='e',fill='#8ea9cb',font=('Segoe UI',8),tags='axes')
        ox,oy=self.screen(0,0)
        # When the origin is offscreen, keep coordinate rulers at the viewport edge.
        ox=max(45,min(width-20,ox)); oy=max(55,min(height-40,oy))
        self.create_line(45,oy,width-12,oy,fill='#7897bf',arrow='last',width=1.5,tags='axes')
        self.create_line(ox,height-40,ox,48,fill='#7897bf',arrow='last',width=1.5,tags='axes')
        self.create_text(width-25,oy-13,text='X',fill='#d8e7fa',tags='axes')
        self.create_text(ox+12,60,text='Y',fill='#d8e7fa',tags='axes')

    def set_stage_position(self, x, y):
        position = (x, y)
        if position == self.stage_position:
            return
        self.stage_position = position
        self.draw_stage_position()

    def draw_stage_position(self):
        self.delete('stage_position')
        x_value, y_value = self.stage_position
        if x_value is None or y_value is None:
            self.create_text(
                self.winfo_width()-18, 18, text='CURRENT XY  unavailable',
                anchor='e', fill='#8294aa', font=('Cascadia Mono',8), tags='stage_position',
            )
            return
        self.create_text(
            self.winfo_width()-18, 18,
            text=f'CURRENT XY  {x_value:.3f}, {y_value:.3f} µm',
            anchor='e', fill='#67e8f9', font=('Cascadia Mono',8,'bold'), tags='stage_position',
        )
        x,y=self.screen(x_value,y_value)
        if 45 <= x <= self.winfo_width()-20 and 55 <= y <= self.winfo_height()-40:
            self.create_line(x-12,y,x+12,y,fill='#22d3ee',width=2,tags='stage_position')
            self.create_line(x,y-12,x,y+12,fill='#22d3ee',width=2,tags='stage_position')
            self.create_oval(x-5,y-5,x+5,y+5,outline='#e0faff',width=2,tags='stage_position')
            self.create_text(
                x+10,y-12,text=f'XY {x_value:.2f}, {y_value:.2f}',anchor='sw',
                fill='#67e8f9',font=('Cascadia Mono',8,'bold'),tags='stage_position',
            )

    def outline(self,points,*,tag='region',color='#60a5fa',draft=False):
        if not points or not hasattr(self.panel,'region_editor'):
            return
        shape=self.panel.shape_var.get()
        method=self.panel.region_editor.method_var.get()
        try:
            if shape=='polygon':
                xy=[v for pt in points for v in self.screen(*pt)]
                if len(points)>=3:
                    self.create_polygon(*xy,fill='',outline=color,width=2,tags=tag)
                elif len(points)>1:
                    self.create_line(*xy,fill=color,width=2,tags=tag)
            else:
                x0,y0,x1,y1=region_geometry(shape,method,points)
                coords=(*self.screen(x0,y0),*self.screen(x1,y1))
                draw=self.create_oval if shape=='circle' else self.create_rectangle
                draw(*coords,outline=color,width=2,tags=tag)
        except ValueError:
            if len(points)>1:
                self.create_line(*[v for pt in points for v in self.screen(*pt)],fill=color,dash=(4,4),tags=tag)
        for index,point in enumerate(points):
            x,y=self.screen(*point)
            self.create_rectangle(x-4,y-4,x+4,y+4,fill=color,outline='#101c2c',tags=tag)
            if not draft:
                label=f'P{index+1}' if index>=2 or method in ('three_points','vertices') else (('C','K')[index] if method=='center_corner' else ('A','B')[index])
                self.create_text(x+9,y-10,text=label,anchor='w',fill=color,tags=tag)

    def draw(self):
        self.delete('all')
        self.point_items={}
        self.draw_axes()
        self.create_text(16,18,text='MAPPING / ORTHOGRAPHIC XY · µm',fill='#d8e7fa',anchor='w',font=('Segoe UI',11,'bold'))
        plan=self.panel.plan
        if plan and (self.panel.running or not self.panel.region_dirty):
            coords=[self.screen(p.stage_x_um,p.stage_y_um) for p in plan.points]
            # Show each acquisition row plus its transition, preserving raster return direction.
            rows=[]
            for point,xy in zip(plan.points,coords):
                if not rows or rows[-1][0]!=point.row:
                    rows.append((point.row,[]))
                rows[-1][1].append(xy)
            previous=None
            for _,row in rows:
                if previous is not None:
                    self.create_line(*previous,*row[0],fill='#526b8e',dash=(3,5),arrow='last',tags='trajectory')
                if len(row)>1:
                    self.create_line(*[v for xy in row for v in xy],fill='#3c729e',arrow='last',arrowshape=(9,11,4),tags='trajectory')
                    # Mid-row arrow stays visible even when endpoints have handles.
                    ax,ay=row[0]; bx,by=row[-1]
                    length=math.hypot(bx-ax,by-ay)
                    if length>35:
                        mx,my=(ax+bx)/2,(ay+by)/2
                        ux,uy=(bx-ax)/length,(by-ay)/length
                        self.create_line(mx-10*ux,my-10*uy,mx+10*ux,my+10*uy,fill='#8bc8f2',width=2,arrow='last',arrowshape=(8,10,4),tags='trajectory')
                previous=row[-1]
            scale,_,_=self.transform()
            radius=max(1,min(4,min(plan.settings.step_x_um,plan.settings.step_y_um)*scale*.18))
            for point,(x,y) in zip(plan.points,coords):
                if not (45<x<self.winfo_width()-20 and 55<y<self.winfo_height()-40):
                    continue
                key=point.row,point.col
                self.point_items[key]=self.create_rectangle(x-radius,y-radius,x+radius,y+radius,fill=COLORS[int(self.panel.states[key])],outline='')
            for xy,label,color in ((coords[0],'START','#34d399'),(coords[-1],'END','#fb7185')):
                self.create_text(xy[0]+8,xy[1]+12,text=label,anchor='w',fill=color,font=('Segoe UI',8,'bold'))
            self.create_text(16,38,text=f'{len(plan.points):,} points · {plan.settings.pattern} · arrows = travel direction',anchor='w',fill='#8ea9cb')
            if self.panel.running:
                settings=plan.settings
                if settings.region_shape=='polygon':
                    self.create_polygon(*[v for pt in settings.polygon_vertices for v in self.screen(*pt)],fill='',outline='#60a5fa',width=2)
                else:
                    draw=self.create_oval if settings.region_shape=='circle' else self.create_rectangle
                    draw(*self.screen(settings.x_a_um,settings.y_a_um),*self.screen(settings.x_b_um,settings.y_b_um),outline='#60a5fa',width=2)
            self.draw_current()
        else:
            self.create_text(16,38,text='Region draft · Preview to generate scan path',anchor='w',fill='#fbbf24')
        if hasattr(self.panel,'region_editor'):
            if self.editing:
                self.outline(self.vertices,color='#fbbf24')
            elif not self.panel.running:
                try:
                    self.outline(self.panel.region_editor.points())
                except ValueError:
                    pass
        if self.selected_target is not None:
            x,y=self.screen(*self.selected_target)
            self.create_oval(x-7,y-7,x+7,y+7,outline='#67e8f9',width=2,tags='selected_target')
            self.create_line(x-11,y,x+11,y,fill='#67e8f9',tags='selected_target')
            self.create_line(x,y-11,x,y+11,fill='#67e8f9',tags='selected_target')
            self.create_text(x+10,y-10,text='MOVE TARGET',anchor='sw',fill='#67e8f9',font=('Segoe UI',8,'bold'),tags='selected_target')
        if hasattr(self.panel,'viewport_var'):
            x0,y0,x1,y1=self.bounds
            self.panel.viewport_var.set(f'X: {x0:.3g} … {x1:.3g} µm\nY: {y0:.3g} … {y1:.3g} µm\nEqual X/Y scale')
        self.draw_stage_position()

    def draw_current(self):
        self.delete('scan_current')
        if self.current:
            x,y=self.screen(self.current.stage_x_um,self.current.stage_y_um)
            self.create_oval(x-9,y-9,x+9,y+9,outline='#fbbf24',width=3,tags=('scan_current','current'))

    def update_point(self,point):
        key=point.row,point.col
        item=getattr(self,'point_items',{}).get(key)
        if item:
            self.itemconfigure(item,fill=COLORS[int(self.panel.states[key])])
