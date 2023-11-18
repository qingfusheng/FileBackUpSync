import datetime
import hashlib
import os
import pathlib
import shutil

from tqdm import tqdm


# Try with pathlib
def walk(base_root_path, root_path, is_source_path):
    # root_path = pathlib.Path(root_path)
    global ignore_dir_paths, ignore_file_paths
    _result = []
    temp_result = []
    # 不知道手动iterdir和直接work，哪个的效率更高
    for each_sub_path in root_path.iterdir():
        _ignore = False
        if each_sub_path.is_dir():
            if is_source_path:
                if any([each_sub_path.is_relative_to(each) for each in ignore_dir_paths]):
                    _ignore = True
                for ignore_dir_path in ignore_dir_paths:
                    if each_sub_path.is_relative_to(ignore_dir_path):
                        _ignore = True
                        break
            if not _ignore:
                temp_result = walk(base_root_path, each_sub_path, is_source_path)
            _result.extend(temp_result)
        elif each_sub_path.is_file():
            if is_source_path:
                if any([each_sub_path.is_relative_to(each) for each in ignore_file_paths]):
                    _ignore = True
            if not _ignore:
                _result.append(each_sub_path.relative_to(base_root_path))
    return _result


def calculate_file_hash(file_path, hash_algorithm="sha256"):
    hasher = hashlib.new(hash_algorithm)
    with open(file_path, "rb") as f:
        buffer_size = 65536  # 64 KB
        while chunk := f.read(buffer_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def compare_file(path1, path2) -> bool():
    return True if calculate_file_hash(path1) == calculate_file_hash(path2) else False


def get_modified_file_list(source_files, target_files):
    """
    status:
    # 0 - 不变
    1 - 修改
    2 - 增加
    3 - 删除
    注意：先判断是否不变，然后判断修改和删除，如果删除则移入回收站，如果修改则移入回收站并增加
    """
    """
    有一种可能，能不能判断是否为rename或者是同文件不同路径，通过hash来判断？后期可能会完善
    """
    modify_list = []

    print("在源目录文件中检索")
    for index in tqdm(range(len(source_files))):
        source_file = source_files[index]
        if source_file in target_files:
            if compare_file:
                modify_list.append([source_file, 0])
            else:
                modify_list.append([source_file, 1])
        else:
            modify_list.append([source_file, 2])

    print("在目标目录文件中检索")
    for index in tqdm(range(len(target_files))):
        target_file = target_files[index]
        if target_file not in source_files:
            modify_list.append([target_file, 3])
    sorted_modified_list = sorted(modify_list, key=lambda x: x[1])
    # with open("modify.txt","w", encoding="utf-8") as f:
    #     for each in sorted_parent_list:
    #         f.write(str(each[1])+"\t"+str(each[0])+"\n")
    return sorted_modified_list


def sync_dir(source_files, target_files):
    modified_list = get_modified_file_list(source_files, target_files)
    delete_list = []
    add_list = []
    print("对修改列表进行遍历")
    for index in tqdm(range(len(modified_list))):
        each_item = modified_list[index]
        if each_item[1] == 0:
            # 表示文件相同，不需要做任何事情
            continue
        elif each_item[1] == 1:
            # 表示文件被修改，需要将原文件删除后重新添加
            delete_list.append(each_item[0])
            add_list.append(each_item[0])
        elif each_item[1] == 2:
            # 表示新建文件，需要添加到备份目录
            add_list.append(each_item[0])
        elif each_item[1] == 3:
            # 表示该文件在CurrentTree中已经被删除，因此需要从备份目录中移除
            delete_list.append(each_item[0])
        else:
            # 常规处理，一般来讲不会发生
            raise ValueError("StatusCode的值域为[0,1,2,3]")

    # 移除文件
    print("对文件进行删除操作")
    recycle_path = target_path.parent.joinpath("个人文档备份回收站", datetime.datetime.now().strftime('%F'))
    if not recycle_path.exists():
        os.mkdir(recycle_path)
    for index in tqdm(range(len(delete_list))):
        _source_file_path = target_path.joinpath(delete_list[index])
        _target_file_path = recycle_path.joinpath(delete_list[index])
        _target_file_path.parent.mkdir(parents=True, exist_ok=True)
        fp.write("Move from {} to {}\n".format(str(_source_file_path), str(_target_file_path)))
        shutil.move(_source_file_path, _target_file_path)
    print("对文件进行添加操作")
    for index in tqdm(range(len(add_list))):
        _source_file_path = source_path.joinpath(add_list[index])
        _target_file_path = target_path.joinpath(add_list[index])
        _target_file_path.parent.mkdir(parents=True, exist_ok=True)
        fp.write("Copy from {} to {}\n".format(str(_source_file_path), str(_target_file_path)))
        if _target_file_path.exists():
            if _target_file_path.is_dir():
                _target_file_path.rmdir()
            else:
                if compare_file(_target_file_path, _source_file_path):
                    continue
        shutil.copy(_source_file_path, _target_file_path.parent)
    return


if __name__ == "__main__":
    fp = open("log_" + datetime.datetime.now().strftime('%F') + ".txt", "w", encoding="utf-8")
    source_path = pathlib.Path(r"E:\个人文档")
    target_path = pathlib.Path(r"I:\Computer_backup\个人文档")

    ignore_dir_pathlib = pathlib.Path("./config/.ignore_dir")
    ignore_file_pathlib = pathlib.Path("./config/.ignore_file")

    # 这里的ignore主要是针对于源路径
    with open(ignore_dir_pathlib, "r", encoding="utf-8") as f:
        ignore_dir_paths = f.readlines()
    ignore_dir_paths = [pathlib.Path(each.strip("\n")) for each in ignore_dir_paths]
    with open(ignore_file_pathlib, "r", encoding="utf-8") as f:
        ignore_file_paths = f.readline()
    ignore_file_paths = [pathlib.Path(each.strip("\n")) for each in ignore_file_paths]

    source_path_files = walk(base_root_path=source_path, root_path=source_path, is_source_path=True)
    target_path_files = walk(base_root_path=target_path, root_path=target_path, is_source_path=False)
    sync_dir(source_path_files, target_path_files)
    fp.close()
