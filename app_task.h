#ifndef __APP_TASK_H
#define __APP_TASK_H

#include "main.h"
#include "system_manager.h"
#include "crawler_master.h"
#include <stdint.h>

/* =========================================================
 *                 应用层任务类型
 * =========================================================
 *
 * 当前应用层要管理的业务任务类型：取书放书
 */
typedef enum
{
    APP_TASK_NONE = 0,
    APP_TASK_FETCH_BOOK,   // 取书任务
    APP_TASK_STORE_BOOK    // 放书任务
} App_TaskType_t;

/* =========================================================
 *                 应用层状态机
 * =========================================================
 *
 * 这是 APP 层的核心状态机。
 * APP 层不再使用“从头跑到尾的阻塞大函数”，
 * 而是采用“每次主循环推进一步”的非阻塞状态机方式。
 *
 */
typedef enum
{
    APP_STATE_IDLE = 0,        // 空闲态，没有任务在执行
    APP_STATE_START,           // 任务启动态，准备按任务类型分支
    APP_STATE_MOVE_Z,          // 发起 Z 轴动作
    APP_STATE_WAIT_Z,          // 等待 Z 轴到位
    APP_STATE_MOVE_X,          // 发起 X 轴动作
    APP_STATE_WAIT_X,          // 等待 X 轴到位
    APP_STATE_RUN_CRAWLER,     // 启动履带
    APP_STATE_WAIT_CRAWLER,    // 等待履带动作完成
    APP_STATE_GRIPPER_ACTION,  // 预留夹爪动作
    APP_STATE_RETURN_Z,        // Z 轴回待机层（当前统一为第一层）
    APP_STATE_WAIT_RETURN_Z,   // 等待 Z 回到待机层
    APP_STATE_RETURN_X,        // X 轴回零位
    APP_STATE_WAIT_RETURN_X,   // 等待 X 轴回零位
    APP_STATE_FINISH,          // 当前任务正常完成
    APP_STATE_ERROR            // 当前任务异常结束
} App_State_t;

/* =========================================================
 *                 楼层定义
 * =========================================================
 *
 * 当前视觉或上位逻辑传给 APP 的楼层号定义。
 */
#define APP_FLOOR_1         1U
#define APP_FLOOR_2         2U

/* =========================================================
 *                 APP 返回值
 * =========================================================
 *
 * APP 层接口返回值定义。
 */
#define APP_RET_OK          0U   // 成功
#define APP_RET_BUSY        1U   // 当前忙，不允许启动新任务
#define APP_RET_PARAM_ERR   2U   // 参数错误
#define APP_RET_FAULT       3U   // 系统处于故障态，不允许启动任务

/* =========================================================
 *                 任务参数结构体
 * =========================================================
 *
 * 当前一次任务的完整输入参数。
 *
 * 设计意义：
 * 1. APP 层不再把流程写死
 * 2. 未来视觉部分只需要给出任务参数
 * 3. 后续加放书时可以直接复用这个结构体
 */
typedef struct
{
    App_TaskType_t task_type;     // 任务类型：取书 / 放书
    uint8_t floor_id;             // 目标楼层：1层 / 2层
    uint8_t compartment_id;       // 目标隔间编号
} App_TaskParam_t;

/* =========================================================
 *                 超时与动作参数
 * =========================================================
 *
 * 这些参数决定应用层等待各设备动作完成的最大时间。
 * 若超时，APP 层会判定本次任务异常，进入错误态。
 */

/* 等待 Z 轴到位超时 */
#define APP_WAIT_Z_TIMEOUT_MS         10000U

/* 等待 X 轴到位超时 */
#define APP_WAIT_X_TIMEOUT_MS         10000U

/* 等待 Z 回待机层超时 */
#define APP_WAIT_RETURN_Z_TIMEOUT_MS  10000U

/* 等待 X 回零位超时 */
#define APP_WAIT_RETURN_X_TIMEOUT_MS  10000U

/* 等待履带动作完成超时 */
#define APP_WAIT_CRAWLER_TIMEOUT_MS   5000U

/* 履带运行时间（当前演示参数） */
#define APP_CRAWLER_RUN_TIME_MS       2000U

/* 测试模式下两次任务之间的间隔 */
#define APP_TEST_INTERVAL_MS          3000U

/* 判断 X 是否回零的允许误差（单位 mm） */
#define APP_X_HOME_POS_TOL_MM         0.5f

/* =========================================================
 *                 对外接口
 * =========================================================
 */

/**
 * @brief APP 层初始化
 *
 * 功能：
 * 1. 初始化 system_manager
 * 2. 初始化 X/Z/Crawler 模块
 * 3. 初始化 APP 层状态机变量
 */
void App_Task_Init(void);

/**
 * @brief 启动一个通用任务
 * @param task 任务参数结构体指针
 * @retval uint8_t
 *
 * 功能：
 * 1. 检查任务参数是否合法
 * 2. 检查系统是否忙 / 是否故障
 * 3. 保存任务参数
 * 4. 启动 APP 状态机
 */
uint8_t App_StartTask(const App_TaskParam_t *task);

/**
 * @brief 启动取放书任务（便捷接口）
 * @param floor_id       目标楼层
 * @param compartment_id 目标隔间
 * @retval uint8_t
 */
uint8_t App_StartFetchTask(uint8_t floor_id, uint8_t compartment_id);
uint8_t App_StartStoreTask(uint8_t floor_id, uint8_t compartment_id);
/**
 * @brief 周期调用，推进 APP 状态机
 *
 * 建议：
 * 在主循环中持续调用该函数。
 
 */

void App_Task_Process(void);

/**
 * @brief 获取当前 APP 状态机状态
 */
App_State_t App_GetState(void);

/**
 * @brief 查询 APP 层当前是否忙
 * @retval 1 忙
 * @retval 0 空闲
 */
uint8_t App_IsBusy(void);

/**
 * @brief 启用/关闭测试模式
 * @param enable 1=启用，0=关闭
 */
void App_TestModeEnable(uint8_t enable);

/**
 * @brief 循环测试任务
 *
 * 功能：
 * 1. 先推进 APP 状态机
 * 2. 若测试模式开启，则按固定顺序自动启动测试任务
 */
void App_Compartment_CycleTest(void);

#endif
