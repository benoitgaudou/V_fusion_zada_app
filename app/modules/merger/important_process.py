#region 0. librairies
from typing import List
import zipfile
import numpy as np
import geopandas as gpd
import pandas as pd
import os
import unicodedata # to remove accent

from pathlib import Path
from pyproj import CRS
from functools import reduce
from scipy import stats as stats
from math import *
from shapely.geometry import MultiPolygon, Polygon, LineString, Point, GeometryCollection
from shapely.ops import unary_union
from itertools import combinations
from itertools import chain
from random import random,seed,shuffle



#endregion
##########
#region 1. useful functions

def convert_to_multipolygon(geom):
    """
    To change the type of a geometrie when it is not working for writing file
    """ 
    if geom.geom_type == 'GeometryCollection' or geom.geom_type == 'MultiLineString' :
        # Extraire uniquement les Polygons de la GeometryCollection
        polygons = [g for g in geom.geoms if g.geom_type == 'Polygon']
        # Convertir en MultiPolygon
        if polygons:
            return MultiPolygon(polygons)
    return geom


def clean_string(s):
    """
    To homogenize caracters - e.g. remove accents, convert all in lower caps
    """ 
    s = str(s)
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.lower()

def contains_linestring(geom):
    """
    Check whether a list of geometries contains lines
    """ 
    if isinstance(geom, GeometryCollection):
        return any(isinstance(g, LineString) for g in geom.geoms)
    return isinstance(geom, LineString)

def filter_geometries(gdf):
    """
    Check and return only valid polygons geometries of a geodataframe (even in geometrycollection), remove other 
    """
    valid_geometries = []
    valid_indices = []   
    for idx, geom in enumerate(gdf.geometry):
        if isinstance(geom, GeometryCollection):
            polygons = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
            if polygons: 
                if len(polygons) == 1:
                    valid_geometries.append(polygons[0])
                else:
                    valid_geometries.append(MultiPolygon(polygons))
                valid_indices.append(idx)
        elif isinstance(geom, (Polygon, MultiPolygon)):
            valid_geometries.append(geom)
            valid_indices.append(idx)
    result_gdf = gdf.iloc[valid_indices].copy()
    result_gdf['geometry'] = valid_geometries
    return result_gdf

def collect_stakes_from_file(path_file, column, list_elements):
    """
    Collect the unique values of a column in a file from path and add them to the list of elements
    """
    i_path=path_file
    vecteur = gpd.read_file(i_path)
    vecteur_df=pd.DataFrame(vecteur)
    vecteur_df = vecteur_df.astype({col: 'str' for col in vecteur_df.columns})
    vecteur_df['Enjeux']=vecteur_df[str(column)].apply(clean_string)
    for j in vecteur_df['Enjeux']:
        elements = j.split(';')
        for elements_split in elements:
            elements_j = elements_split.split(',')
            for z in elements_j:
                list_elements.append(z)
                # To find elements in vectors : 
                if z == "superposition_gaz_lgv":
                    print("superposition_gaz_lgv", vecteur_df["Zada"].loc[0])
    return(list_elements)

#endregion
##########
#region 2. collect shp folder

def collect_shp_files(path_folder_in):
    # collect the path of shp file in zip files :
    list_gdf=[]
    for path_file in Path(path_folder_in).iterdir():
        if not path_file.is_file() : # check the path targets a file
            continue        
        if path_file.suffix.lower() != '.zip': # check the file is a .zip
            continue
        with zipfile.ZipFile(path_file, 'r') as z:
            for info in z.infolist():
                # if '.shp' in info.filename:
                if Path(info.filename).suffix.lower() == '.shp':
                    uri = f"/vsizip/{path_file}/{info.filename}"
                    gdf = gpd.read_file(uri)
                    list_gdf.append(gdf)
    return(list_gdf)

#endregion
##########
#region 3. col category


def col_classif(list_gdf: List[gpd.GeoDataFrame], col_to_remove=[]):
#    list_gdf = collect_shp_files(folder_in)
    if list_gdf != []:
        gdf_i=list_gdf[0]
        col_to_sum=[]
        col_to_str=[]
        table_i=gdf_i.drop(columns=['geometry'])
        for col in table_i.columns: 
            if col not in col_to_remove:
                if table_i[col].dtype == 'object' or pd.api.types.is_string_dtype(table_i[col]):
                    col_to_str.append(col)
                elif pd.api.types.is_numeric_dtype(table_i[col]):
                    col_to_sum.append(col)
                else:
                    col_to_remove.append(col)
        return(col_to_sum, col_to_str, col_to_remove)        
    else : 
        print('empty list of vectors')
        return

#endregion
##########
#region 4. intra-overlap

#### To fusion overlapping polygons in a single zada file vector (shp) :
#### Not sure if it works when more than two vectors overlap in a given shp....

# collect all zada shp file paths

#def intra_overlap_clean(folder_in, col_zada='zada', folder_out='modified/', col_to_remove=[]):
def intra_overlap_clean(list_gdf: List[gpd.GeoDataFrame], col_zada='zada', col_to_remove=[]) -> List[gpd.GeoDataFrame]:
    i=1
    print('i test', i)
#    list_gdf = collect_shp_files(folder_in)

    list_gdf_out = []
    if list_gdf == []:
        print('empty list of vectors')
        return
    else : 
        col_to_sum, col_to_str, col_to_remove = col_classif(list_gdf, col_to_remove)
        print('col_to_sum', col_to_sum)
        zada_with_intersect=[]
#        folder_out_path = folder_in + folder_out
#        os.makedirs(folder_out_path, exist_ok=True)
        for gdf_i in list_gdf: # for each file : read and merge overlapping vectors
            # print(gdf_i.drop(columns=['geometry']).columns)
            zada_1=filter_geometries(gdf_i)
            print('zada columns', zada_1.columns)
            # clean columns
            for j in col_to_sum:
                if j in zada_1.columns:
                    s = zada_1[j].astype(str).str.strip()
                    s = s.str.replace("'", "", regex=False).str.replace("\u00A0", "", regex=False)
                    s = s.str.replace(",", ".", regex=False)            
                    zada_1[j] = pd.to_numeric(s, errors='coerce').fillna(0).astype(int)
            #
            intersections = []
#            intersect=[]
            for idx_a, idx_b in combinations(range(len(zada_1)), 2): # look whether vectors overlap two by two 
                geom_z1 = zada_1.geometry.iloc[idx_a]
                geom_z2 = zada_1.geometry.iloc[idx_b]
                if geom_z1 is not None and geom_z2 is not None:
                    intersection_geom = geom_z1.intersection(geom_z2)
                    if not intersection_geom.is_empty:  # Check whether there is an intersection
                        zada_with_intersect.append(zada_1)
                        intersection_data = {
                            'geometry': intersection_geom
                            # 'ZADA': f"{zada_1['ZADA'].iloc[idx_a]}"
                        }
                        # For quantitative columns : NOT SUM for those columns : just keep 1 if already 1
                        for j in col_to_sum:
                            # Take just 1 if one on both is 1, and 0 if not (don't sum because sum = number of zada)
                            if pd.isna(zada_1[str(j)].iloc[idx_a]):
                                intersect_sum= zada_1[str(j)].iloc[idx_b]
                                print("na")
                            elif pd.isna(zada_1[str(j)].iloc[idx_b]): 
                                intersect_sum= zada_1[str(j)].iloc[idx_a]
                                print("na")
                            else :
                                print("not na", zada_1, j)
                                # zada_1[str(j)] = pd.to_numeric(zada_1[str(j)].astype(str).str.strip().str.replace("'", "", regex=False),errors='coerce').fillna(0).astype(int)
                                if (zada_1[str(j)].iloc[idx_a])>0 or (zada_1[str(j)].iloc[idx_b])>0:
                                    intersect_sum = 1
                                else:
                                    intersect_sum = 0
                            if pd.isna(intersect_sum):
                                intersection_data[j] = pd.NA
                            else : 
                                intersection_data[j] = int(intersect_sum)
                        # For qualitative variables : Paste str for those columns
                        for j in col_to_str:
                            if j in zada_1.columns:
                                if j.lower() != col_zada:
                                    if str(zada_1[str(j)].iloc[idx_a]) == '0' or pd.isna(zada_1[str(j)].iloc[idx_a]):
                                        intersect_str_j = str(zada_1[str(j)].iloc[idx_b])
                                    elif str(zada_1[str(j)].iloc[idx_b]) == '0'or pd.isna(zada_1[str(j)].iloc[idx_b]):
                                        intersect_str_j = str(zada_1[str(j)].iloc[idx_a])
                                    else:
                                        intersect_str_j = str(zada_1[str(j)].iloc[idx_a]) + '-' + str(zada_1[str(j)].iloc[idx_b])
                                else:
                                    if pd.isna(zada_1[str(j)].iloc[idx_a]):
                                        intersect_str_j=pd.NA
                                    else:
                                        intersect_str_j = str(zada_1[str(j)].iloc[idx_a])
                            intersection_data[j] = intersect_str_j
                        intersections.append(intersection_data)
            # With all collected intersections : 
            if intersections == []: # if no intersection, keep the vector as it is
                merged_geometry_1_2 = list(zada_1.geometry)
                final_attributes = pd.concat([zada_1],ignore_index=True) 
            else: # if there is at least 1 intersection : convert geometries to keep only the polygons
                intersect_vector_1_2 = gpd.GeoDataFrame(intersections, crs=zada_1.crs)
                if 'Point' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='Point']
                if 'MultiPoint' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='MultiPoint']
                if 'LineString' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='LineString']
                if 'MultiLineString' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='MultiLineString']
                if 'GeometryCollection' in  intersect_vector_1_2['geometry'].geom_type.unique():
                    valid_geom_list = filter_geometries(intersect_vector_1_2)
                    intersect_vector_1_2=valid_geom_list.copy()
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].apply(lambda geom: isinstance(geom, (Polygon, MultiPolygon)))]
                # for None type entities:
                # print(intersect_vector_1_2['geometry'].geom_type.value_counts())
                if intersect_vector_1_2.empty: # check whether if remains no polygon
                    merged_geometry_1_2 = list(zada_1.geometry)
                    final_attributes = pd.concat([zada_1],ignore_index=True) # just put one under the other
                else: # if it reamins at least 1 valid polygon
                    empty_geoms = intersect_vector_1_2[intersect_vector_1_2['geometry'].is_empty]
                    if not empty_geoms.empty:
                        intersect_vector_1_2= intersect_vector_1_2[~intersect_vector_1_2['geometry'].is_empty]
                    intersect_vector_1_2['geometry'] = intersect_vector_1_2['geometry'].apply(convert_to_multipolygon)
                    # geometrical fusion : difference between the initial vector and the intersection, then merge (=fusion) resulting difference and the intersection
                    zada_1_diff = gpd.overlay(zada_1, intersect_vector_1_2, how='difference')
                    zada_1_diff = zada_1_diff[~zada_1_diff['geometry'].apply(contains_linestring)] # remove string
                    zada_1_diff['geometry'] = zada_1_diff['geometry'].apply(convert_to_multipolygon) # convert "geometrycollection" entities in polygon if they are 
                    # 
                    merged_geometry_1_2 = list(zada_1_diff.geometry) + list(intersect_vector_1_2.geometry) # combined geometry
                    # Create final geodf
                    final_attributes = pd.concat([zada_1_diff,intersect_vector_1_2], ignore_index=True)                    
            # export the resulting shp
            merged_vector_1_2=gpd.GeoDataFrame(geometry=merged_geometry_1_2)
            merged_vector_1_2.reset_index(drop=True, inplace=True)
            merged_vector_1_2_join= merged_vector_1_2.join(final_attributes.drop(columns=['geometry']))
            merged_vector_1_2_join.set_crs(epsg="2154", inplace=True)
            path=f"zada_intra_intersect_{i}.shp"
            i=i+1
            list_gdf_out.append(merged_vector_1_2_join)
#            merged_vector_1_2_join.to_file(folder_out_path + path)
        return(list_gdf_out)


#endregion
##########
#region 5. Fusion zada

def fusion_zada(list_gdf_in: List[gpd.GeoDataFrame], col_zada='zada', col_to_remove=[]) -> gpd.GeoDataFrame:
#    folder_modified=intra_overlap_clean(folder_in, col_zada=col_zada, folder_out=folder_ir_file, col_to_remove=col_to_remove)
    list_gdf_modified=intra_overlap_clean(list_gdf_in, col_zada=col_zada, col_to_remove=col_to_remove)
    if not list_gdf_modified:
        print("list_gdf is empty or None")
        return None   # ou raise, ou sys.exit(1)
    else :
        double=[]
#        folder_ir_file_path=folder_modified  
#        list_gdf=[]
#        for path_file in Path(folder_ir_file_path).iterdir():
#            if path_file.suffix.lower() == '.shp':
#                gdf_i=gpd.read_file(path_file)
#                list_gdf.append(gdf_i)
        col_to_sum, col_to_str, col_to_remove = col_classif(list_gdf_modified, col_to_remove)
        zada_with_intersect=[]
#        folder_out_path = folder_in + folder_out
#        os.makedirs(folder_out_path, exist_ok=True)
        zada_merged=[]
        # initialised the final vector with first file
        zada_1=list_gdf_modified[0]
        # for each file : merge with the previous resulting merged file 
        for i in range(1,(int(len(list_gdf_modified)))):    
            # read new file
            zada_2=list_gdf_modified[i]  
            # keep valid geometries of the shp  
            zada_2=filter_geometries(zada_2)
            # initialise intersections list
            intersections = []
            intersect=[]
            # for each polygon, check whether there is intersection two by two
            for idx_a, geom_z1 in enumerate(zada_1.geometry):
                for idx_b, geom_z2 in enumerate(zada_2.geometry):
                    if geom_z1 is not None and geom_z2 is not None:
                        intersection_geom = geom_z1.intersection(geom_z2)
                        if not intersection_geom.is_empty:  # check whether there is an intersection
                            intersection_data = {
                                'geometry': intersection_geom
                                # 'ZADA': f"{zada_1['ZADA'].iloc[idx_a]}-{zada_2['ZADA'].iloc[idx_b]}" # keep information on which zada are overlapping
                            }
                            #
                            for j in col_to_sum: # for quantitative variables : sum the value (1 = presence / 0 = absence) 
                                if j in zada_1.columns and j in zada_2.columns:
                                    if pd.isna(zada_1[str(j)].iloc[idx_a]):
                                        intersect_sum= zada_1[str(j)].iloc[idx_b]
                                    elif pd.isna(zada_2[str(j)].iloc[idx_b]): 
                                        intersect_sum= zada_1[str(j)].iloc[idx_a]
                                    else :
                                        intersect_sum = float(zada_1[str(j)].iloc[idx_a]) + float(zada_2[str(j)].iloc[idx_b])
                                    if pd.isna(intersect_sum):
                                        intersection_data[j] = pd.NA
                                    else : 
                                        intersection_data[j] = int(intersect_sum)
                            #
                            for j in col_to_str: # for qualitative variable : paste the value of each polygon 
                                if j in zada_1.columns and j in zada_2.columns:
                                    if str(zada_1[str(j)].iloc[idx_a]) == '0' or pd.isna(zada_1[str(j)].iloc[idx_a]):
                                        intersect_str_j = str(zada_2[str(j)].iloc[idx_b])
                                    elif str(zada_2[str(j)].iloc[idx_b]) == '0' or pd.isna(zada_2[str(j)].iloc[idx_b]):
                                        intersect_str_j = str(zada_1[str(j)].iloc[idx_a])
                                    else:
                                        intersect_str_j = str(zada_1[str(j)].iloc[idx_a]) + '-' + str(zada_2[str(j)].iloc[idx_b])
                                    intersection_data[j] = intersect_str_j
                            intersections.append(intersection_data)
            # Create gdf from intersections
            if intersections == []: # if no intersections keep the shp as it is
                merged_geometry_1_2 = list(zada_1.geometry) + list(zada_2.geometry)
                final_attributes = pd.concat([zada_1, zada_2],ignore_index=True) # just put one under the other
            else: # if there is at least one intersection
                intersect_vector_1_2 = gpd.GeoDataFrame(intersections, crs=zada_1.crs)
                # remove all geometries that are not polygons
                if 'Point' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='Point']
                if 'MultiPoint' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='MultiPoint']
                if 'LineString' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='LineString']
                if 'MultiLineString' in intersect_vector_1_2['geometry'].geom_type.unique():
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].geom_type !='MultiLineString']
                if 'GeometryCollection' in  intersect_vector_1_2['geometry'].geom_type.unique(): # need to check whether polygons are in geometrycollection entities
                    valid_geom_list = filter_geometries(intersect_vector_1_2) 
                    intersect_vector_1_2=valid_geom_list.copy()
                    intersect_vector_1_2 = intersect_vector_1_2[intersect_vector_1_2['geometry'].apply(lambda geom: isinstance(geom, (Polygon, MultiPolygon)))]
                # for None type entities:
                empty_geoms = intersect_vector_1_2[intersect_vector_1_2['geometry'].is_empty]
                if not empty_geoms.empty:
                    intersect_vector_1_2= intersect_vector_1_2[~intersect_vector_1_2['geometry'].is_empty]
                intersect_vector_1_2['geometry'] = intersect_vector_1_2['geometry'].apply(convert_to_multipolygon)
                # geometrical fusion : difference between the main vector and the intersections, then merge (=fusion) resulting difference and the intersection
                zada_1_diff = gpd.overlay(zada_1, intersect_vector_1_2, how='difference') # warning : need to move by hand on QGIS the node to the next one : 372259.25001912325 6382544.4007045636 0 
                zada_1_diff = zada_1_diff[~zada_1_diff['geometry'].apply(contains_linestring)]
                zada_1_diff['geometry'] = zada_1_diff['geometry'].apply(convert_to_multipolygon)
                # geometrical fusion : difference between the initial second vector and the intersections, then merge (=fusion) resulting difference and the intersection
                zada_2_diff = gpd.overlay(zada_2, intersect_vector_1_2, how='difference')
                zada_2_diff = zada_2_diff[~zada_2_diff['geometry'].apply(contains_linestring)]
                zada_2_diff['geometry'] = zada_2_diff['geometry'].apply(convert_to_multipolygon)
                # merge both cut vectors with the intersections geometries
                merged_geometry_1_2 = list(zada_1_diff.geometry) + list(zada_2_diff.geometry) + list(intersect_vector_1_2.geometry)
                # Create final geodf
                final_attributes = pd.concat([zada_1_diff, zada_2_diff,intersect_vector_1_2], ignore_index=True)
            ## end of the else ##
            # export final shp
            merged_vector_1_2=gpd.GeoDataFrame(geometry=merged_geometry_1_2)
            merged_vector_1_2.reset_index(drop=True, inplace=True)
            merged_vector_1_2_join= merged_vector_1_2.join(final_attributes.drop(columns=['geometry']))
            merged_vector_1_2_join.set_crs(epsg="2154", inplace=True)
            # change zada_1 to do the fusion with one another shp
            zada_1=merged_vector_1_2_join
#        merged_vector_1_2_join.to_file(folder_out_path + "fusion_zada.shp")
        return(merged_vector_1_2_join)

#endregion
##########
#region 6. test zada data

# ZADA Landes de Gascogne 
# folder_in = "C:/Users/DELL-Precision/OneDrive - Ecole d'Ingénieurs de PURPAN/Documents/Dynafor/Perforssa/Automatisation/Test_outil/test_fonction_fusion/import1/"
# col_to_remove=['Type', 'Prospectiv']

# merged_vector_1_2_join=fusion_zada(folder_in, col_to_remove=col_to_remove)


# ZADA Albi

# folder_in = "C:/Users/DELL-Precision/OneDrive - Ecole d'Ingénieurs de PURPAN/Documents/Dynafor/Perforssa/Automatisation/Test_outil/test_fonction_fusion/2021-2023-2025-ZADA-France-Albi/zip"
# merged_vector_1_2_join=fusion_zada(folder_in)

# not any table have similar columns...


# ZADA Bénin

# not any table have similar columns...

# ZAda Mexique 
# test with epuisement var 
# folder_in = "C:/Users/DELL-Precision/OneDrive - Ecole d'Ingénieurs de PURPAN/Documents/Dynafor/Perforssa/Automatisation/Test_outil/test_fonction_fusion/Epuisement/zip/"

# merged_vector_1_2_join=fusion_zada(folder_in)

