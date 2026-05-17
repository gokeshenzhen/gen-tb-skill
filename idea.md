# 做一个通用的uvm testbench auto-generative skill

## 统一的目录结构有助于环境规范化,并减少因环境因素增加学习成本,参照 /home/robin/Projects/vcs-verification-of-apb-based-uart-master-core 

### rtl 放入设计文件如
### script 放脚本相关
### tb 放testbench 以及 UVC
### test 放测试用例
### top 放顶层文件
### work 放编译仿真,波形等runtime结果文件

## 用户为DV和DE或没有任何背景的芯片工程师, 要是编译不通过他们调试起来比较费劲, 是否需要spawn sub-agent保证这个环境编译能通过

## 用户提供设计spec, skill根据设计spec生成reference model, 由于用户可能是 DE 对UVM不熟悉,他们使用这个tb的方式类似于调用task来进行, 所以是否支持到bus function model或者可以在module方便调用UVM component task的方式来进行, 这样DV可以采用sequence/svcase, DE使用task的方式来用这个tb

## 是否需要什么reference给到这个skill

## skill-creator这个skill有哪些可供参考,或用到这个skill里面
