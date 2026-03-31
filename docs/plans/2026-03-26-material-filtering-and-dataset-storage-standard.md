# 素材过滤与长期存储标准

> 更新时间：2026-03-30  
> 录制素材根目录：`/Volumes/990 PRO PCIe 4T/match_plan_recordings`  
> 长期素材库根目录：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library`

## 1. 目标

这份文档用于定义：

- 什么素材是**真正合格**的，可反复打磨和长期留存
- 什么素材只能作为待复核
- 什么素材必须淘汰，不能进入样本、评测和训练链

当前结论：**不是所有 `bound` 素材都合格**，还必须通过时间覆盖率校验。

## 2. 严格过滤规则

一条素材只有同时满足下面条件，才算合格：

0. 当前默认只保留 `FT`（足球）素材
1. `status = completed`
2. `data_binding_status = bound`
3. `matched_rows > 0`
4. `full.mp4` 存在
5. `__timeline.csv` 存在
6. `__sync_viewer.html` 存在
7. **时间覆盖率 >= 0.95**

时间覆盖率定义：

`timeline_last_elapsed / video_duration_sec`

含义：

- 视频结束前，数据时间线必须基本跟到视频末尾
- 如果后面视频还很长，但 timeline 只覆盖前面一小段，这条素材不能进入黄金样本

## 3. 三档分类

### Gold（可直接长期留存）

满足全部严格规则，尤其是时间覆盖率 `>= 0.95`。

用途：

- 黄金样本
- 固定评测集
- 训练候选池
- 反复打磨使用

### Silver Review（待人工复核）

满足：

- `completed + bound + matched_rows > 0`
- 有 timeline 和 sync viewer
- 但时间覆盖率在 `0.60 ~ 0.95` 之间

用途：

- 作为复核候选
- 人工确认是否只截取前段可用窗口
- 不能直接进入黄金样本

### Reject（淘汰）

包含：

- unbound/test-only
- 没有 timeline
- 没有 sync viewer
- 时间覆盖率 < 0.60
- 视频后半段没有数据推进

这些素材：

- 不进入样本库
- 不进入评测集
- 不进入训练池

## 4. 当前盘点结果

- 扫描录制流总数：`752`
- Gold：`58`
- Silver Review：`24`
- Reject：`625`

### 当前 Gold 素材

- **Paysandu x Miramar**：覆盖率 `1.057`，匹配数据 `1787`，视频 [FT_Paysandu_vs_Miramar__2026-03-27_14-52-32__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_145232/FT_Paysandu_vs_Miramar_2026-03-27_14-52-32/FT_Paysandu_vs_Miramar__2026-03-27_14-52-32__full.mp4)
- **Argentina x Mauritania**：覆盖率 `1.007`，匹配数据 `1781`，视频 [FT_Argentina_vs_Mauritania__20260327_162313_969738__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_162313_969738/FT_Argentina_vs_Mauritania_20260327_162313_969738/FT_Argentina_vs_Mauritania__20260327_162313_969738__full.mp4)
- **Bucaramanga x Santa Fe**：覆盖率 `1.050`，匹配数据 `1780`，视频 [FT_Bucaramanga_vs_Santa_Fe__20260327_181549_816303__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_181549_816303/FT_Bucaramanga_vs_Santa_Fe_20260327_181549_816303/FT_Bucaramanga_vs_Santa_Fe__20260327_181549_816303__full.mp4)
- **Belgrano Cordoba x Atletico DE Rafaela**：覆盖率 `1.050`，匹配数据 `1780`，视频 [FT_Chaco_For_Ever_vs_Gimnasia_Jujuy__20260327_181549_816303__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_181549_816303/FT_Chaco_For_Ever_vs_Gimnasia_Jujuy_20260327_181549_816303/FT_Chaco_For_Ever_vs_Gimnasia_Jujuy__20260327_181549_816303__full.mp4)
- **Sportivo Ameliano x Libertad Asuncion**：覆盖率 `1.009`，匹配数据 `1779`，视频 [FT_Sportivo_Ameliano_vs_Libertad_Asuncion__20260327_163123_459563__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_163123_459563/FT_Sportivo_Ameliano_vs_Libertad_Asuncion_20260327_163123_459563/FT_Sportivo_Ameliano_vs_Libertad_Asuncion__20260327_163123_459563__full.mp4)
- **San Martin S.J. x Racing Cordoba**：覆盖率 `1.015`，匹配数据 `1774`，视频 [FT_San_Martin_S.J._vs_Racing_Cordoba__20260327_192512_856624__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_192512_856624/FT_San_Martin_S.J._vs_Racing_Cordoba_20260327_192512_856624/FT_San_Martin_S.J._vs_Racing_Cordoba__20260327_192512_856624__full.mp4)
- **Chaco For Ever x Gimnasia Jujuy**：覆盖率 `1.015`，匹配数据 `1774`，视频 [FT_Chaco_For_Ever_vs_Gimnasia_Jujuy__20260327_192208_366736__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_192208_366736/FT_Chaco_For_Ever_vs_Gimnasia_Jujuy_20260327_192208_366736/FT_Chaco_For_Ever_vs_Gimnasia_Jujuy__20260327_192208_366736__full.mp4)
- **Venados FC x Atlante FC**：覆盖率 `1.014`，匹配数据 `1762`，视频 [FT_Venados_FC_vs_Atlante_FC__20260327_185411_586257__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_185411_586257/FT_Venados_FC_vs_Atlante_FC_20260327_185411_586257/FT_Venados_FC_vs_Atlante_FC__20260327_185411_586257__full.mp4)
- **Recoleta x Deportes Temuco**：覆盖率 `0.978`，匹配数据 `1724`，视频 [FT_Recoleta_vs_Deportes_Temuco__20260327_152434_289855__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_152434_289855/FT_Recoleta_vs_Deportes_Temuco_20260327_152434_289855/FT_Recoleta_vs_Deportes_Temuco__20260327_152434_289855__full.mp4)
- **Fortaleza FC x Deportivo Pasto**：覆盖率 `0.999`，匹配数据 `521`，视频 [FT_Fortaleza_FC_vs_Deportivo_Pasto__2026-03-24_19-26-12__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-24/session_20260324_192612/FT_Fortaleza_FC_vs_Deportivo_Pasto_2026-03-24_19-26-12/FT_Fortaleza_FC_vs_Deportivo_Pasto__2026-03-24_19-26-12__full.mp4)
- **Morocco x Ecuador**：覆盖率 `1.165`，匹配数据 `216`，视频 [FT_Morocco_vs_Ecuador__2026-03-27_14-49-08__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_144908/FT_Morocco_vs_Ecuador_2026-03-27_14-49-08/FT_Morocco_vs_Ecuador__2026-03-27_14-49-08__full.mp4)
- **Bahamas x Anguilla**：覆盖率 `2.511`，匹配数据 `68`，视频 [FT_Bahamas_vs_Anguilla__20260326_150615_519896__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_150615_519896/FT_Bahamas_vs_Anguilla_20260326_150615_519896/FT_Bahamas_vs_Anguilla__20260326_150615_519896__full.mp4)
- **Moldova x Lithuania**：覆盖率 `1.084`，匹配数据 `62`，视频 [FT_Moldova_vs_Lithuania__20260326_080204_584247__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_080204_584247/FT_Moldova_vs_Lithuania_20260326_080204_584247/FT_Moldova_vs_Lithuania__20260326_080204_584247__full.mp4)
- **Escorpiones Belén x CS Uruguay**：覆盖率 `4.396`，匹配数据 `32`，视频 [FT_Escorpiones_Belén_vs_CS_Uruguay__20260326_144409_243597__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_144409_243597/FT_Escorpiones_Belén_vs_CS_Uruguay_20260326_144409_243597/FT_Escorpiones_Belén_vs_CS_Uruguay__20260326_144409_243597__full.mp4)
- **Wales x Bosnia & Herzegovina**：覆盖率 `4.396`，匹配数据 `32`，视频 [FT_Brazil_vs_France__20260326_144409_243597__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_144409_243597/FT_Brazil_vs_France_20260326_144409_243597/FT_Brazil_vs_France__20260326_144409_243597__full.mp4)
- **Slovakia x Kosovo**：覆盖率 `1.190`，匹配数据 `29`，视频 [FT_Martinique_vs_Cuba__20260326_140253_045888__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_140253_045888/FT_Martinique_vs_Cuba_20260326_140253_045888/FT_Martinique_vs_Cuba__20260326_140253_045888__full.mp4)
- **Poland x Albania**：覆盖率 `2.739`，匹配数据 `21`，视频 [FT_Poland_vs_Albania__20260326_144041_688911__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_144041_688911/FT_Poland_vs_Albania_20260326_144041_688911/FT_Poland_vs_Albania__20260326_144041_688911__full.mp4)
- **Martinique x Cuba**：覆盖率 `2.739`，匹配数据 `21`，视频 [FT_Martinique_vs_Cuba__20260326_144041_688911__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_144041_688911/FT_Martinique_vs_Cuba_20260326_144041_688911/FT_Martinique_vs_Cuba__20260326_144041_688911__full.mp4)
- **Avai vs Cianorte**：覆盖率 `0.000`，匹配数据 `21`，视频 [FT_Avai_vs_Cianorte__pgstapp_20260329_142429_Avai_vs_Cianorte__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_142429_Avai_vs_Cianorte/FT_Avai_vs_Cianorte__pgstapp_20260329_142429_Avai_vs_Cianorte/FT_Avai_vs_Cianorte__pgstapp_20260329_142429_Avai_vs_Cianorte__full.mp4)
- **Bonaire vs Saint Martin**：覆盖率 `0.000`，匹配数据 `20`，视频 [FT_Bonaire_vs_Saint_Martin__pgstapp_20260329_173008_Bonaire_vs_Saint_Martin__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_173008_Bonaire_vs_Saint_Martin/FT_Bonaire_vs_Saint_Martin__pgstapp_20260329_173008_Bonaire_vs_Saint_Martin/FT_Bonaire_vs_Saint_Martin__pgstapp_20260329_173008_Bonaire_vs_Saint_Martin__full.mp4)
- **Famalicão U23 x Sporting CP U23**：覆盖率 `1.216`，匹配数据 `19`，视频 [FT_Famalicão_U23_vs_Sporting_CP_U23__20260326_081404_367124__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_081404_367124/FT_Famalicão_U23_vs_Sporting_CP_U23_20260326_081404_367124/FT_Famalicão_U23_vs_Sporting_CP_U23__20260326_081404_367124__full.mp4)
- **Botafogo PB vs ASA**：覆盖率 `0.000`，匹配数据 `19`，视频 [FT_Botafogo_PB_vs_ASA__pgstapp_20260329_143023_Botafogo_PB_vs_ASA__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_143023_Botafogo_PB_vs_ASA/FT_Botafogo_PB_vs_ASA__pgstapp_20260329_143023_Botafogo_PB_vs_ASA/FT_Botafogo_PB_vs_ASA__pgstapp_20260329_143023_Botafogo_PB_vs_ASA__full.mp4)
- **CS Uruguay vs Cariari Pococi**：覆盖率 `0.000`，匹配数据 `19`，视频 [FT_CS_Uruguay_vs_Cariari_Pococi__pgstapp_20260329_120040_CS_Uruguay_vs_Cariari_Po__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120040_CS_Uruguay_vs_Cariari_Po/FT_CS_Uruguay_vs_Cariari_Pococi__pgstapp_20260329_120040_CS_Uruguay_vs_Cariari_Po/FT_CS_Uruguay_vs_Cariari_Pococi__pgstapp_20260329_120040_CS_Uruguay_vs_Cariari_Po__full.mp4)
- **Club Guarani vs Sportivo Trinidense**：覆盖率 `0.000`，匹配数据 `18`，视频 [FT_Club_Guarani_vs_Sportivo_Trinidense__pgstapp_20260329_150020_Club_Guarani_vs_Sportivo__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_150020_Club_Guarani_vs_Sportivo/FT_Club_Guarani_vs_Sportivo_Trinidense__pgstapp_20260329_150020_Club_Guarani_vs_Sportivo/FT_Club_Guarani_vs_Sportivo_Trinidense__pgstapp_20260329_150020_Club_Guarani_vs_Sportivo__full.mp4)
- **Newells Old Boys vs Acassuso**：覆盖率 `0.000`，匹配数据 `18`，视频 [FT_Newells_Old_Boys_vs_Acassuso__pgstapp_20260329_161917_Newells_Old_Boys_vs_Acas__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_161917_Newells_Old_Boys_vs_Acas/FT_Newells_Old_Boys_vs_Acassuso__pgstapp_20260329_161917_Newells_Old_Boys_vs_Acas/FT_Newells_Old_Boys_vs_Acassuso__pgstapp_20260329_161917_Newells_Old_Boys_vs_Acas__full.mp4)
- **San Telmo vs Quilmes**：覆盖率 `0.000`，匹配数据 `18`，视频 [FT_San_Telmo_vs_Quilmes__pgstapp_20260329_120021_San_Telmo_vs_Quilmes__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120021_San_Telmo_vs_Quilmes/FT_San_Telmo_vs_Quilmes__pgstapp_20260329_120021_San_Telmo_vs_Quilmes/FT_San_Telmo_vs_Quilmes__pgstapp_20260329_120021_San_Telmo_vs_Quilmes__full.mp4)
- **Penarol vs Racing Montevideo**：覆盖率 `0.000`，匹配数据 `17`，视频 [FT_Penarol_vs_Racing_Montevideo__pgstapp_20260329_153019_Penarol_vs_Racing_Montev__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_153019_Penarol_vs_Racing_Montev/FT_Penarol_vs_Racing_Montevideo__pgstapp_20260329_153019_Penarol_vs_Racing_Montev/FT_Penarol_vs_Racing_Montevideo__pgstapp_20260329_153019_Penarol_vs_Racing_Montev__full.mp4)
- **British Virgin Islands vs Anguilla**：覆盖率 `0.000`，匹配数据 `17`，视频 [FT_British_Virgin_Islands_vs_Anguilla__pgstapp_20260329_143246_British_Virgin_Islands_v__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_143246_British_Virgin_Islands_v/FT_British_Virgin_Islands_vs_Anguilla__pgstapp_20260329_143246_British_Virgin_Islands_v/FT_British_Virgin_Islands_vs_Anguilla__pgstapp_20260329_143246_British_Virgin_Islands_v__full.mp4)
- **Greece x Paraguay**：覆盖率 `8.665`，匹配数据 `16`，视频 [FT_Greece_vs_Paraguay__20260327_123855_369915__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_123855_369915/FT_Greece_vs_Paraguay_20260327_123855_369915/FT_Greece_vs_Paraguay__20260327_123855_369915__full.mp4)
- **Belgium U21 x Austria U21**：覆盖率 `7.299`，匹配数据 `16`，视频 [FT_Belgium_U21_vs_Austria_U21__20260327_123855_369915__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_123855_369915/FT_Belgium_U21_vs_Austria_U21_20260327_123855_369915/FT_Belgium_U21_vs_Austria_U21__20260327_123855_369915__full.mp4)
- **Almeria vs Real Sociedad II**：覆盖率 `0.000`，匹配数据 `16`，视频 [FT_Almeria_vs_Real_Sociedad_II__pgstapp_20260329_120103_Almeria_vs_Real_Sociedad__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120103_Almeria_vs_Real_Sociedad/FT_Almeria_vs_Real_Sociedad_II__pgstapp_20260329_120103_Almeria_vs_Real_Sociedad/FT_Almeria_vs_Real_Sociedad_II__pgstapp_20260329_120103_Almeria_vs_Real_Sociedad__full.mp4)
- **Newells Old Boys vs Acassuso D**：覆盖率 `0.000`，匹配数据 `16`，视频 [FT_Newells_Old_Boys_vs_Acassuso_D__pgstapp_20260329_161556_Newells_Old_Boys_vs_Acas__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_161556_Newells_Old_Boys_vs_Acas/FT_Newells_Old_Boys_vs_Acassuso_D__pgstapp_20260329_161556_Newells_Old_Boys_vs_Acas/FT_Newells_Old_Boys_vs_Acassuso_D__pgstapp_20260329_161556_Newells_Old_Boys_vs_Acas__full.mp4)
- **Cayman Islands vs Bahamas**：覆盖率 `0.000`，匹配数据 `15`，视频 [FT_Cayman_Islands_vs_Bahamas__pgstapp_20260329_170317_Cayman_Islands_vs_Bahama__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_170317_Cayman_Islands_vs_Bahama/FT_Cayman_Islands_vs_Bahamas__pgstapp_20260329_170317_Cayman_Islands_vs_Bahama/FT_Cayman_Islands_vs_Bahamas__pgstapp_20260329_170317_Cayman_Islands_vs_Bahama__full.mp4)
- **Cobreloa vs Union San Felipe D**：覆盖率 `0.000`，匹配数据 `15`，视频 [FT_Cobreloa_vs_Union_San_Felipe_D__pgstapp_20260329_153044_Cobreloa_vs_Union_San_Fe__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_153044_Cobreloa_vs_Union_San_Fe/FT_Cobreloa_vs_Union_San_Felipe_D__pgstapp_20260329_153044_Cobreloa_vs_Union_San_Fe/FT_Cobreloa_vs_Union_San_Felipe_D__pgstapp_20260329_153044_Cobreloa_vs_Union_San_Fe__full.mp4)
- **Deportivo Maipu vs Godoy Cruz**：覆盖率 `0.000`，匹配数据 `15`，视频 [FT_Deportivo_Maipu_vs_Godoy_Cruz__pgstapp_20260329_130018_Deportivo_Maipu_vs_Godoy__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_130018_Deportivo_Maipu_vs_Godoy/FT_Deportivo_Maipu_vs_Godoy_Cruz__pgstapp_20260329_130018_Deportivo_Maipu_vs_Godoy/FT_Deportivo_Maipu_vs_Godoy_Cruz__pgstapp_20260329_130018_Deportivo_Maipu_vs_Godoy__full.mp4)
- **Brazil x France**：覆盖率 `3.932`，匹配数据 `14`，视频 [FT_Brazil_vs_France__20260326_130336_970388__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-26/session_20260326_130336_970388/FT_Brazil_vs_France_20260326_130336_970388/FT_Brazil_vs_France__20260326_130336_970388__full.mp4)
- **ABC vs Sport Recife**：覆盖率 `0.000`，匹配数据 `13`，视频 [FT_ABC_vs_Sport_Recife__pgstapp_20260329_130041_ABC_vs_Sport_Recife__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_130041_ABC_vs_Sport_Recife/FT_ABC_vs_Sport_Recife__pgstapp_20260329_130041_ABC_vs_Sport_Recife/FT_ABC_vs_Sport_Recife__pgstapp_20260329_130041_ABC_vs_Sport_Recife__full.mp4)
- **Gubbio vs Ravenna**：覆盖率 `0.000`，匹配数据 `13`，视频 [FT_Gubbio_vs_Ravenna__pgstapp_20260329_113023_Gubbio_vs_Ravenna__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_113023_Gubbio_vs_Ravenna/FT_Gubbio_vs_Ravenna__pgstapp_20260329_113023_Gubbio_vs_Ravenna/FT_Gubbio_vs_Ravenna__pgstapp_20260329_113023_Gubbio_vs_Ravenna__full.mp4)
- **Bolívar vs Marítimo**：覆盖率 `0.000`，匹配数据 `13`，视频 [FT_Bolívar_vs_Marítimo__pgstapp_20260329_120122_Bolívar_vs_Marítimo__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120122_Bolívar_vs_Marítimo/FT_Bolívar_vs_Marítimo__pgstapp_20260329_120122_Bolívar_vs_Marítimo/FT_Bolívar_vs_Marítimo__pgstapp_20260329_120122_Bolívar_vs_Marítimo__full.mp4)
- **Internacional de Bogota vs Junior**：覆盖率 `0.000`，匹配数据 `13`，视频 [FT_Internacional_de_Bogota_vs_Junior__pgstapp_20260329_141025_Internacional_de_Bogota___full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_141025_Internacional_de_Bogota_/FT_Internacional_de_Bogota_vs_Junior__pgstapp_20260329_141025_Internacional_de_Bogota_/FT_Internacional_de_Bogota_vs_Junior__pgstapp_20260329_141025_Internacional_de_Bogota___full.mp4)
- **NJ/NY Gotham FC W vs Orlando Pride W**：覆盖率 `0.000`，匹配数据 `13`，视频 [FT_NJ_NY_Gotham_FC_W_vs_Orlando_Pride_W__pgstapp_20260329_161006_NJ_NY_Gotham_FC_W_vs_Orl__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_161006_NJ_NY_Gotham_FC_W_vs_Orl/FT_NJ_NY_Gotham_FC_W_vs_Orlando_Pride_W__pgstapp_20260329_161006_NJ_NY_Gotham_FC_W_vs_Orl/FT_NJ_NY_Gotham_FC_W_vs_Orlando_Pride_W__pgstapp_20260329_161006_NJ_NY_Gotham_FC_W_vs_Orl__full.mp4)
- **Fortuna Ålesund W x Stabæk W**：覆盖率 `2.010`，匹配数据 `12`，视频 [FT_Fortuna_Ålesund_W_vs_Stabæk_W__20260328_050934_717775__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050934_717775/FT_Fortuna_Ålesund_W_vs_Stabæk_W_20260328_050934_717775/FT_Fortuna_Ålesund_W_vs_Stabæk_W__20260328_050934_717775__full.mp4)
- **Atletico Avila FC vs Monagas II**：覆盖率 `0.000`，匹配数据 `12`，视频 [FT_Atletico_Avila_FC_vs_Monagas_II__pgstapp_20260329_130101_Atletico_Avila_FC_vs_Mon__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_130101_Atletico_Avila_FC_vs_Mon/FT_Atletico_Avila_FC_vs_Monagas_II__pgstapp_20260329_130101_Atletico_Avila_FC_vs_Mon/FT_Atletico_Avila_FC_vs_Monagas_II__pgstapp_20260329_130101_Atletico_Avila_FC_vs_Mon__full.mp4)
- **Real Madrid W vs Barcelona W**：覆盖率 `0.000`，匹配数据 `11`，视频 [FT_Real_Madrid_W_vs_Barcelona_W__pgstapp_20260329_120048_Real_Madrid_W_vs_Barcelo__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120048_Real_Madrid_W_vs_Barcelo/FT_Real_Madrid_W_vs_Barcelona_W__pgstapp_20260329_120048_Real_Madrid_W_vs_Barcelo/FT_Real_Madrid_W_vs_Barcelona_W__pgstapp_20260329_120048_Real_Madrid_W_vs_Barcelo__full.mp4)
- **Puerto Cabello II vs Real Frontera**：覆盖率 `0.000`，匹配数据 `11`，视频 [FT_Puerto_Cabello_II_vs_Real_Frontera__pgstapp_20260329_123041_Puerto_Cabello_II_vs_Rea__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_123041_Puerto_Cabello_II_vs_Rea/FT_Puerto_Cabello_II_vs_Real_Frontera__pgstapp_20260329_123041_Puerto_Cabello_II_vs_Rea/FT_Puerto_Cabello_II_vs_Real_Frontera__pgstapp_20260329_123041_Puerto_Cabello_II_vs_Rea__full.mp4)
- **Nueva Chicago vs All Boys**：覆盖率 `0.000`，匹配数据 `9`，视频 [FT_Nueva_Chicago_vs_All_Boys__pgstapp_20260329_113157_Nueva_Chicago_vs_All_Boy__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_113157_Nueva_Chicago_vs_All_Boy/FT_Nueva_Chicago_vs_All_Boys__pgstapp_20260329_113157_Nueva_Chicago_vs_All_Boy/FT_Nueva_Chicago_vs_All_Boys__pgstapp_20260329_113157_Nueva_Chicago_vs_All_Boy__full.mp4)
- **Martinique vs El Salvador**：覆盖率 `0.000`，匹配数据 `9`，视频 [FT_Martinique_vs_El_Salvador__pgstapp_20260329_120150_Martinique_vs_El_Salvado__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120150_Martinique_vs_El_Salvado/FT_Martinique_vs_El_Salvador__pgstapp_20260329_120150_Martinique_vs_El_Salvado/FT_Martinique_vs_El_Salvador__pgstapp_20260329_120150_Martinique_vs_El_Salvado__full.mp4)
- **China PR U23 x Korea DPR U23**：覆盖率 `1.375`，匹配数据 `8`，视频 [FT_China_PR_U23_vs_Korea_DPR_U23__20260328_050728_520339__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050728_520339/FT_China_PR_U23_vs_Korea_DPR_U23_20260328_050728_520339/FT_China_PR_U23_vs_Korea_DPR_U23__20260328_050728_520339__full.mp4)
- **Namibia x Comoros**：覆盖率 `1.370`，匹配数据 `8`，视频 [FT_Namibia_vs_Comoros__20260328_050728_520339__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050728_520339/FT_Namibia_vs_Comoros_20260328_050728_520339/FT_Namibia_vs_Comoros__20260328_050728_520339__full.mp4)
- **Hønefoss W x Molde W**：覆盖率 `1.187`，匹配数据 `8`，视频 [FT_Hønefoss_W_vs_Molde_W__20260328_050728_520339__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050728_520339/FT_Hønefoss_W_vs_Molde_W_20260328_050728_520339/FT_Hønefoss_W_vs_Molde_W__20260328_050728_520339__full.mp4)
- **Everton W x Liverpool W**：覆盖率 `1.145`，匹配数据 `8`，视频 [FT_Everton_W_vs_Liverpool_W__20260328_050728_520339__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050728_520339/FT_Everton_W_vs_Liverpool_W_20260328_050728_520339/FT_Everton_W_vs_Liverpool_W__20260328_050728_520339__full.mp4)
- **Røa W x Bodø / Glimt W**：覆盖率 `1.119`，匹配数据 `8`，视频 [FT_Røa_W_vs_Bodø_Glimt_W__20260328_050728_520339__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050728_520339/FT_Røa_W_vs_Bodø_Glimt_W_20260328_050728_520339/FT_Røa_W_vs_Bodø_Glimt_W__20260328_050728_520339__full.mp4)
- **Haugesund W x Vålerenga W**：覆盖率 `1.045`，匹配数据 `8`，视频 [FT_Haugesund_W_vs_Vålerenga_W__20260328_050728_520339__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-28/session_20260328_050728_520339/FT_Haugesund_W_vs_Vålerenga_W_20260328_050728_520339/FT_Haugesund_W_vs_Vålerenga_W__20260328_050728_520339__full.mp4)
- **Colombia vs France**：覆盖率 `0.000`，匹配数据 `8`，视频 [FT_Colombia_vs_France__pgstapp_20260329_120225_Colombia_vs_France__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-29/session_pgstapp_20260329_120225_Colombia_vs_France/FT_Colombia_vs_France__pgstapp_20260329_120225_Colombia_vs_France/FT_Colombia_vs_France__pgstapp_20260329_120225_Colombia_vs_France__full.mp4)
- **Azerbaijan x St. Lucia**：覆盖率 `5.922`，匹配数据 `5`，视频 [FT_Germany_U21_vs_Northern_Ireland_U21__20260327_100255_592068__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_100255_592068/FT_Germany_U21_vs_Northern_Ireland_U21_20260327_100255_592068/FT_Germany_U21_vs_Northern_Ireland_U21__20260327_100255_592068__full.mp4)
- **Austria x Ghana**：覆盖率 `5.259`，匹配数据 `5`，视频 [FT_Austria_vs_Ghana__20260327_100255_592068__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_100255_592068/FT_Austria_vs_Ghana_20260327_100255_592068/FT_Austria_vs_Ghana__20260327_100255_592068__full.mp4)
- **Norway U21 x Netherlands U21**：覆盖率 `4.179`，匹配数据 `5`，视频 [FT_Norway_U21_vs_Netherlands_U21__20260327_100255_592068__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_100255_592068/FT_Norway_U21_vs_Netherlands_U21_20260327_100255_592068/FT_Norway_U21_vs_Netherlands_U21__20260327_100255_592068__full.mp4)
- **Kazakhstan U21 x Slovakia U21**：覆盖率 `4.150`，匹配数据 `5`，视频 [FT_Montenegro_vs_Andorra__20260327_100255_592068__full.mp4](/Volumes/990 PRO PCIe 4T/match_plan_recordings/2026-03-27/session_20260327_100255_592068/FT_Montenegro_vs_Andorra_20260327_100255_592068/FT_Montenegro_vs_Andorra__20260327_100255_592068__full.mp4)

### 当前 Silver Review 候选（最佳代表）

- **CA La Paz x CDS Tampico Madero**：覆盖率 `0.787`，匹配数据 `1358`，需人工判断是否截取局部窗口使用
- **Newport County x Shrewsbury**：覆盖率 `0.918`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Reading x Wigan**：覆盖率 `0.917`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Wycombe x Port Vale**：覆盖率 `0.916`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Barnet x Cambridge United**：覆盖率 `0.916`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Granada CF x Huesca**：覆盖率 `0.916`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Stockport County x AFC Wimbledon**：覆盖率 `0.916`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Exeter City x Leyton Orient**：覆盖率 `0.914`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Atlanta x Ferro Carril Oeste**：覆盖率 `0.914`，匹配数据 `127`，需人工判断是否截取局部窗口使用
- **Valladolid x Burgos**：覆盖率 `0.913`，匹配数据 `127`，需人工判断是否截取局部窗口使用

## 5. 长期素材库目录

长期素材库根目录：`/Volumes/990 PRO PCIe 4T/match_plan_dataset_library`

- `00_docs`
- `01_gold_matches`
- `02_silver_review_queue`
- `03_rejected_materials`
- `04_golden_samples/clips`
- `04_golden_samples/labels`
- `04_golden_samples/meta`
- `05_eval_sets`
- `06_training_pool`
- `07_benchmarks`
- `08_model_outputs`
- `09_reviews`
- `10_manifests`

目录用途：

- `01_gold_matches`：记录严格合格比赛，不直接塞杂项
- `02_silver_review_queue`：待人工复核的素材
- `03_rejected_materials`：明确淘汰的素材索引
- `04_golden_samples`：后续真正切出来的 clip/label/meta
- `05_eval_sets`：固定评测集
- `06_training_pool`：训练候选池
- `10_manifests`：自动生成的全量清单和最佳素材索引

## 6. 自动化脚本

后续不再建议手工整理。请直接使用脚本：`/Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py`

示例：

```bash
python3 /Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py
```

脚本会自动：

- 扫描录制目录
- 刷新 Gold/Silver/Reject
- 更新 manifests
- 刷新长期素材库入口目录
- 重写这份过滤标准文档

## 7. 立即执行建议

- 现在不要直接训练。
- 先只从 **Gold** 素材里切第一批 clip。
- `Silver Review` 先人工复核，再决定是否局部截取使用。
- `Reject` 统一淘汰，不再混入后续流程。
