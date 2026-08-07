#ifndef A72373AD_81A8_4982_A0E3_D90C8254F1FD
#define A72373AD_81A8_4982_A0E3_D90C8254F1FD
#include "Device.h"
#include "TypeDefine.h"
#include <vector>
namespace basic
{
    namespace dsp
    {
        /**
         * @brief 脑电负载检测（佩戴检测）
         * 
         * @param inputData 原始数据
         * @param deviceInfo 设备信息
         * @return 是否有负载（是否佩戴）
         */
        bool eegLoadCheck(const vectord &inputData, DeviceInfo *deviceInfo);

        /// 压电压阻负载检测（佩戴检测）
        /// \param prData 压阻原始数据
        /// \return 是否有负载（是否佩戴）
        bool peprLoadCheck(const std::vector<int>& prData);
        /**
         * @brief 数据单离群点剔除（适用于离群点不连续出现的情况）
         * 
         * @param inputData 原始数据
         * @return vectord 处理后数据
         */
        vectord singleOutlierRemove(const vectord &inputData);

        /**
         * @brief 数据双离群点剔除（适用于离群点连续出现两次的情况）
         * 
         * @param inputData 原始数据
         * @return vectord 处理后数据
         */
        vectord doubleOutlierRemove(const vectord &inputData);

        /**
         * @brief 电压幅值计算
         * 
         * @param inputData 原始数据
         * @param maxVolt 设备最大电压值
         * @param minVolt 设备最小电压值
         * @param maxVal 设备最大采样值
         * @param minVal 设备最小采样值
         * @return vectord 电压值
         */
        vectord voltageCal(const vectord &inputData, double maxVolt, double minVolt, int maxVal, int minVal);

        /**
         * @brief 按长度将原始数据分割为片段（注意，最后一段数据的长度可能会小于 chunk_size）
         * 
         * @param inputData 输入的原始数据
         * @param chunkSize 每个数据块的长度
         * @param stepSize 每次数据窗移动的距离
         * @return std::vector<std::vector<double>> 分割后的每段数据
         */
        std::vector<std::vector<double>> splitByIndex(const vectord &inputData, int chunkSize, int stepSize);

        /**
         * @brief 按时间将原始数据分割为片段（注意，最后一段数据的长度可能会小于 chunk_size）
         * 
         * @param inputData 输入的原始数据
         * @param chunkSec 数据片段长度（单位：秒）
         * @param stepSec 数据窗滑动长度（单位：秒）
         * @param fs 采样率
         * @return std::vector<std::vector<double>> 分片数据
         */
        std::vector<std::vector<double>> splitByTime(const vectord &inputData, float chunkSec, float stepSec, float fs);
    } // namespace dsp
    
} // namespace basic


#endif /* A72373AD_81A8_4982_A0E3_D90C8254F1FD */
